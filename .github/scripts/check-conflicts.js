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

  async function hasLabel(prNumber) {
    const { data } = await github.rest.issues.listLabelsOnIssue({
      owner, repo, issue_number: prNumber,
    });
    return data.some(l => l.name === LABEL);
  }

  function buildConflictComment(pr) {
    return [
      MARKER,
      '⚠️ **This PR has a merge conflict — a quick rebase will unblock it**',
      '',
      '`main` has moved since this branch was opened. Reviewers can\'t merge until the conflict is resolved.',
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
    const body     = buildConflictComment(pr);
    const existing = await findBotComment(pr.number);

    if (existing) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
    } else {
      await github.rest.issues.createComment({ owner, repo, issue_number: pr.number, body });
      core.info(`Posted conflict comment on PR #${pr.number}.`);
    }

    if (!(await hasLabel(pr.number))) {
      await github.rest.issues.addLabels({ owner, repo, issue_number: pr.number, labels: [LABEL] });
      core.info(`Added "${LABEL}" to PR #${pr.number}.`);
    }
  }

  async function clearConflict(pr) {
    const [existing, labeled] = await Promise.all([
      findBotComment(pr.number),
      hasLabel(pr.number),
    ]);

    if (!existing && !labeled) return;

    if (existing) {
      await github.rest.issues.deleteComment({ owner, repo, comment_id: existing.id });
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
