// @ts-check
'use strict';

const { section } = require('./lib/markdown');
const { swapLabel } = require('./lib/labels');
const { findComment, deleteComment, upsertComment } = require('./lib/comment');

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const body   = context.payload.pull_request.body || '';
  const prNum  = context.payload.pull_request.number;
  const MARKER = '<!-- pr-description-check-bot -->';
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;

  const problems = [];

  // 1. Summary must be filled in.
  if (section(body, 'Summary').length < 20) {
    problems.push('**Summary** is empty or too short — describe what changed and why.');
  }

  // 2. Linked Issue must reference a real issue. Accept a bare #NNN, a closing
  //    keyword + #NNN, or a full issue URL (e.g. .../issues/123) — the strict
  //    keyword-prefixed form previously false-flagged correctly-linked PRs.
  const linkedSection = section(body, 'Linked Issue');
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
  const howTo = section(body, 'How to Test');
  if (howTo.length < 30) {
    problems.push('**How to Test** — explain how a reviewer can verify this change. Numbered steps, the commands you ran, or a short code block all work — give a sentence or two of real detail (not just "tested locally").');
  }

  // ── Comment ──────────────────────────────────────────────────────────────
  if (problems.length === 0) {
    await deleteComment(github, owner, repo, prNum, MARKER);
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

    await upsertComment(github, owner, repo, prNum, MARKER, commentBody);
  }

  // ── Labels ────────────────────────────────────────────────────────────────
  if (problems.length === 0) {
    await swapLabel(github, core, owner, repo, prNum, 'ready for review', 'needs work');
  } else {
    await swapLabel(github, core, owner, repo, prNum, 'needs work', 'ready for review');
    core.setFailed(`PR description has ${problems.length} issue(s) — see bot comment for details.`);
  }
};
