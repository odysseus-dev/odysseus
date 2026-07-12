// @ts-check
'use strict';

const { section } = require('./lib/markdown');
const { addLabel, dropLabel } = require('./lib/labels');
const { upsertComment, deleteComment } = require('./lib/comment');

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const issue  = context.payload.issue;
  const body   = (issue.body || '').trim();
  const labels = issue.labels.map(l => l.name);
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;
  const num    = issue.number;

  const isBug     = labels.includes('bug');
  const isFeature = labels.includes('enhancement');

  const failures = [];

  // ── Common: body must exist ───────────────────────────────────────────────
  if (body.length < 50) {
    failures.push(
      '**Description** — body is empty or too short. ' +
      'Please open the issue using one of the provided templates.',
    );
  }

  // An issue is one or the other — never both. Resolve to a single type so the
  // validation can't run two conflicting blocks at once.
  const type = isBug && isFeature ? 'conflict' : isBug ? 'bug' : isFeature ? 'feature' : 'untyped';

  switch (type) {
    case 'conflict':
      failures.push('**Labels** — an issue cannot be both `bug` and `enhancement`. Remove one label.');
      break;

    case 'bug': {
      if (!section(body, 'Install Method')) {
        failures.push('**Install Method** — select how you installed Odysseus');
      }

      if (!section(body, 'Operating System')) {
        failures.push('**Operating System** — select your OS');
      }

      const stepsText = section(body, 'Steps to Reproduce');
      if (!stepsText || !/\d+\.|[-*]/.test(stepsText)) {
        failures.push('**Steps to Reproduce** — must include at least one numbered or bulleted step');
      }

      if (section(body, 'Expected Behaviour').length < 10) {
        failures.push('**Expected Behaviour** — section is empty or too short');
      }

      if (section(body, 'Actual Behaviour').length < 10) {
        failures.push('**Actual Behaviour** — section is empty or too short');
      }
      break;
    }

    case 'feature':
      if (!section(body, 'Area')) {
        failures.push('**Area** — select which part of the application this affects');
      }

      if (section(body, 'Problem or Motivation').length < 20) {
        failures.push(
          '**Problem or Motivation** — section is empty or too short ' +
          '(explain the concrete problem this solves)',
        );
      }

      if (section(body, 'Proposed Solution').length < 20) {
        failures.push(
          '**Proposed Solution** — section is empty or too short ' +
          '(describe the change you want to see)',
        );
      }

      if (!section(body, 'Are you willing to implement this?')) {
        failures.push('**Are you willing to implement this?** — select an option');
      }
      break;

    // 'untyped' → only the common body-length check applies.
  }

  // ── Unfilled dropdowns ────────────────────────────────────────────────────
  // #2068 added a "-- Please Select --" default to every template dropdown, so
  // a contributor who never opens the dropdown submits with that literal string
  // as the section value. The per-section checks above only verify presence, so
  // a placeholder value passes. Scan every section and flag the ones still
  // showing the placeholder, as a single comma-separated line item.
  const PLACEHOLDER = '-- Please Select --';
  const headingRe = /^#+\s+(.+?)\s*$/gm;
  const headings = [];
  let headingMatch;
  while ((headingMatch = headingRe.exec(body)) !== null) {
    headings.push({
      name: headingMatch[1].trim(),
      headStart: headingMatch.index,
      contentStart: headingMatch.index + headingMatch[0].length,
    });
  }
  const unfilled = [];
  for (let i = 0; i < headings.length; i++) {
    const end = i + 1 < headings.length ? headings[i + 1].headStart : body.length;
    if (body.slice(headings[i].contentStart, end).includes(PLACEHOLDER)) {
      unfilled.push(headings[i].name);
    }
  }
  if (unfilled.length > 0) {
    failures.push(
      `**Unfilled dropdowns** — please choose a value; these sections still show ` +
      `the \`${PLACEHOLDER}\` placeholder: ${unfilled.join(', ')}.`,
    );
  }

  // ── Labels + Comment ─────────────────────────────────────────────────────
  const MARKER = '<!-- issue-description-check -->';
  const LABEL_BAD  = 'needs more info';
  const LABEL_GOOD = 'ready for review';

  if (failures.length === 0) {
    await deleteComment(github, owner, repo, num, MARKER);
    await dropLabel(github, owner, repo, num, LABEL_BAD);
    await addLabel(github, core, owner, repo, num, LABEL_GOOD);
  } else {
    const list = failures.map(f => `- ${f}`).join('\n');
    const commentBody = [
      MARKER,
      '⚠️ **Issue description is incomplete.** Please update the following sections:',
      '',
      list,
      '',
      '_This comment is deleted automatically once all sections are complete._',
    ].join('\n');

    await upsertComment(github, owner, repo, num, MARKER, commentBody);
    await dropLabel(github, owner, repo, num, LABEL_GOOD);
    await addLabel(github, core, owner, repo, num, LABEL_BAD);

    core.setFailed(`Issue description has ${failures.length} issue(s) — see bot comment for details.`);
  }
};
