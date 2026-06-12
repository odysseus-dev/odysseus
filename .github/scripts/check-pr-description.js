// @ts-check
'use strict';

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const body   = context.payload.pull_request.body || '';
  const prNum  = context.payload.pull_request.number;
  const MARKER = '<!-- pr-description-check-bot -->';
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;

  // Strip HTML comments so placeholder text does not count as content.
  function strip(text) {
    return (text ?? '').replace(/<!--[\s\S]*?-->/g, '').trim();
  }

  // Extract the text content of a Section. Matches any heading depth (#, ##,
  // ###, …) so the check doesn't break if the template's heading level changes.
  function section(heading) {
    const m = body.match(new RegExp(`#+\\s+${heading}[\\s\\S]*?(?=\\n#+\\s+|$)`, 'i'));
    return strip(m?.[0].replace(new RegExp(`#+\\s+${heading}`, 'i'), '') ?? '');
  }

  const problems = [];

  // 1. Summary must be filled in.
  if (section('Summary').length < 20) {
    problems.push('**Summary** is empty or too short — describe what changed and why.');
  }

  // 2. Linked Issue must reference a real issue. Accept a bare #NNN, a closing
  //    keyword + #NNN, or a full issue URL (e.g. .../issues/123) — the strict
  //    keyword-prefixed form previously false-flagged correctly-linked PRs.
  const linkedSection = section('Linked Issue');
  const hasIssueRef = /#\d+\b/.test(linkedSection) || /\/issues\/\d+/.test(linkedSection);
  if (!linkedSection || !hasIssueRef) {
    problems.push('**Linked Issue** — add a reference like `Fixes #NNN`, a bare `#NNN`, or a link to the issue.');
  }

  // 3. At least one Type of Change box must be checked.
  const typeBlock = body.match(/##\s+Type of Change[\s\S]*?(?=\n##\s|$)/i)?.[0] ?? '';
  if (!/- \[x\]/i.test(typeBlock)) {
    problems.push('**Type of Change** — check at least one box.');
  }

  // 4. Duplicate-search checklist item must be checked.
  if (!/- \[x\] I searched/i.test(body)) {
    problems.push('**Checklist** — check the duplicate-search box to confirm you searched existing issues and PRs.');
  }

  // 5. How to Test must contain enough real detail for a reviewer to act on.
  //    Any format is fine — numbered steps, prose, the commands you ran, or a
  //    code block — so we only require non-trivial content, not a specific shape.
  const howTo = section('How to Test');
  if (howTo.length < 30) {
    problems.push('**How to Test** — explain how a reviewer can verify this change. Numbered steps, the commands you ran, or a short code block all work — give a sentence or two of real detail (not just "tested locally").');
  }

  // ── Comment ──────────────────────────────────────────────────────────────
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: prNum, per_page: 100,
  });
  const existing = comments.find(c => (c.body ?? '').includes(MARKER));

  if (problems.length === 0) {
    if (existing) {
      await github.rest.issues.deleteComment({ owner, repo, comment_id: existing.id });
    }
  } else {
    const commentBody = [
      MARKER,
      '⚠️ **PR description — action needed**',
      '',
      'The following required sections are missing or incomplete. Please update the PR description to address them:',
      '',
      problems.map(p => `- ${p}`).join('\n'),
      '',
      '---',
      '_This comment is deleted automatically once all sections are complete._',
    ].join('\n');

    if (existing) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body: commentBody });
    } else {
      await github.rest.issues.createComment({ owner, repo, issue_number: prNum, body: commentBody });
    }
  }

  // ── Labels ────────────────────────────────────────────────────────────────
  // These labels are expected to already exist in the repo — managing the
  // repo's label set is the maintainer's job, not this workflow's. We check a
  // label exists before applying it (issues.addLabels would otherwise silently
  // create a missing label) and fail soft — warn and skip — if it's absent.
  async function labelExists(name) {
    try {
      await github.rest.issues.getLabel({ owner, repo, name });
      return true;
    } catch (e) {
      if (e.status === 404) return false;
      throw e;
    }
  }

  async function swapLabel(num, add, remove) {
    if (await labelExists(add)) {
      try {
        await github.rest.issues.addLabels({ owner, repo, issue_number: num, labels: [add] });
      } catch (e) {
        // Fail soft on a token that can't write labels so a label permission
        // problem never masks the actual description verdict.
        if (e.status !== 403) throw e;
        core.warning(`Could not add "${add}" — token lacks label write here; skipping.`);
      }
    } else {
      core.warning(`Label "${add}" does not exist in the repo — skipping. Create it once to enable labelling.`);
    }
    try {
      await github.rest.issues.removeLabel({ owner, repo, issue_number: num, name: remove });
    } catch (e) {
      if (e.status !== 404 && e.status !== 410 && e.status !== 403) throw e;
    }
  }

  if (problems.length === 0) {
    await swapLabel(prNum, 'ready for review', 'needs work');
  } else {
    await swapLabel(prNum, 'needs work', 'ready for review');
    core.setFailed(`PR description has ${problems.length} issue(s) — see bot comment for details.`);
  }
};
