// @ts-check
'use strict';

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;
  const MARKER = '<!-- pr-conflict-check-bot -->';
  const LABEL  = 'needs-rebase';

  // ── Helpers ───────────────────────────────────────────────────────────────

  async function ensureLabelExists() {
    try {
      await github.rest.issues.getLabel({ owner, repo, name: LABEL });
    } catch (e) {
      if (e.status === 404) {
        await github.rest.issues.createLabel({
          owner, repo,
          name:        LABEL,
          color:       'e11d48',
          description: 'This PR has merge conflicts and needs a rebase.',
        });
        core.info(`Created label "${LABEL}".`);
      } else {
        throw e;
      }
    }
  }

  async function fetchAllOpenPRs() {
    const query = `
      query($owner: String!, $repo: String!, $cursor: String) {
        repository(owner: $owner, name: $repo) {
          pullRequests(
            states: [OPEN]
            first: 100
            after: $cursor
            orderBy: { field: UPDATED_AT, direction: DESC }
          ) {
            pageInfo { hasNextPage endCursor }
            nodes {
              number
              mergeable
              baseRefName
              author { login ... on Bot { __typename } }
              labels(first: 100) { nodes { name } }
              comments(first: 50, orderBy: { field: UPDATED_AT, direction: DESC }) {
                nodes { databaseId body }
              }
            }
          }
        }
      }
    `;

    const results = [];
    let cursor = null;
    do {
      const { repository } = await github.graphql(query, { owner, repo, cursor });
      const page = repository.pullRequests;
      results.push(...page.nodes);
      cursor = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null;
    } while (cursor);

    return results;
  }

  async function findBotComment(prNumber) {
    const comments = await github.paginate(github.rest.issues.listComments, {
      owner, repo, issue_number: prNumber, per_page: 100,
    });
    return comments.find(c => (c.body ?? '').includes(MARKER)) ?? null;
  }

  // GraphQL gives us labels and the most recent comments for free in the same
  // page that lists open PRs. Most PRs in the queue are clean and have neither
  // the label nor a marker comment, so this lets clearConflict() skip the two
  // REST round-trips (listComments + listLabelsOnIssue) for the common case —
  // the queue scans hundreds of PRs per run and only a fraction need mutation.
  function hydratedState(pr) {
    const labelNodes   = pr.labels?.nodes ?? [];
    const commentNodes = pr.comments?.nodes ?? [];
    const marker = commentNodes.find(c => (c.body ?? '').includes(MARKER)) ?? null;
    return {
      hasLabel: labelNodes.some(l => l.name === LABEL),
      markerCommentId: marker ? marker.databaseId : null,
      // The comments connection is capped at 50, newest first — if a PR has
      // more than that, an old marker comment could sit outside the page and
      // we can't trust "no marker found here" as "no marker exists".
      commentsMayBeIncomplete: commentNodes.length >= 50,
    };
  }

  function buildConflictComment(pr) {
    return [
      MARKER,
      '⚠️ **This PR has a merge conflict — a quick rebase will unblock it**',
      '',
      '`dev` has moved since this branch was opened. Reviewers can\'t merge until the conflict is resolved.',
      '',
      '**To fix it:**',
      '',
      '```bash',
      'git fetch origin',
      'git rebase origin/dev',
      '# resolve any conflicts, then:',
      'git rebase --continue',
      'git push --force-with-lease',
      '```',
      '',
      'If you prefer a merge instead:',
      '',
      '```bash',
      'git fetch origin',
      'git merge origin/dev',
      '# resolve conflicts, commit, then:',
      'git push',
      '```',
      '',
      'Once the conflict is gone, this comment will be deleted automatically.',
      '',
      '_Not sure how to resolve a conflict? GitHub\'s [resolving merge conflicts](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/addressing-merge-requests/resolving-a-merge-conflict-using-the-command-line) guide walks through it step by step._',
    ].join('\n');
  }

  async function flagConflict(pr) {
    const body  = buildConflictComment(pr);
    const state = hydratedState(pr);
    const existingId = state.markerCommentId
      ?? (state.commentsMayBeIncomplete ? (await findBotComment(pr.number))?.id ?? null : null);

    if (existingId) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existingId, body });
    } else {
      await github.rest.issues.createComment({ owner, repo, issue_number: pr.number, body });
      core.info(`Posted conflict comment on PR #${pr.number}.`);
    }

    if (!state.hasLabel) {
      await github.rest.issues.addLabels({ owner, repo, issue_number: pr.number, labels: [LABEL] });
      core.info(`Added "${LABEL}" to PR #${pr.number}.`);
    }
  }

  async function clearConflict(pr) {
    const state = hydratedState(pr);

    // Nothing to clear, and the comment page we have is complete enough to
    // trust that absence — skip the REST round-trips entirely.
    if (!state.hasLabel && !state.markerCommentId && !state.commentsMayBeIncomplete) {
      return;
    }

    const existingId = state.markerCommentId
      ?? (state.commentsMayBeIncomplete ? (await findBotComment(pr.number))?.id ?? null : null);
    const labeled = state.hasLabel;

    if (!existingId && !labeled) return;

    if (existingId) {
      await github.rest.issues.deleteComment({ owner, repo, comment_id: existingId });
    }

    if (labeled) {
      try {
        await github.rest.issues.removeLabel({ owner, repo, issue_number: pr.number, name: LABEL });
        core.info(`Cleared "${LABEL}" from PR #${pr.number} — conflict resolved.`);
      } catch (e) {
        if (e.status !== 404 && e.status !== 410) throw e;
      }
    }
  }

  // ── Main ──────────────────────────────────────────────────────────────────

  await ensureLabelExists();

  const allPrs = await fetchAllOpenPRs();
  const prs = allPrs.filter(pr => pr.baseRefName === 'dev');
  core.info(`Scanning ${prs.length} open PR(s) targeting dev for merge conflicts…`);

  let flagged = 0;
  let cleared = 0;

  for (const pr of prs) {
    // Skip bot-authored PRs — they manage their own lifecycle.
    if (pr.author?.__typename === 'Bot') continue;

    // GitHub computes mergeability asynchronously after each push. UNKNOWN means
    // the result isn't ready yet. Skip safely — the schedule trigger will catch it.
    if (pr.mergeable === 'UNKNOWN') {
      core.info(`PR #${pr.number}: mergeability still computing, skipping.`);
      continue;
    }

    if (pr.mergeable === 'CONFLICTING') {
      await flagConflict(pr);
      flagged++;
    } else {
      await clearConflict(pr);
      if (pr.mergeable === 'MERGEABLE') cleared++;
    }
  }

  core.info(`Done. Flagged: ${flagged}, cleared: ${cleared}, total scanned: ${prs.length}.`);
};
