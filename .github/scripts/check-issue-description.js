// @ts-check
'use strict';

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const issue  = context.payload.issue;
  const body   = (issue.body || '').trim();
  const labels = issue.labels.map(l => l.name);
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;

  const isBug     = labels.includes('bug');
  const isFeature = labels.includes('enhancement');

  // Extract a Section's text, stripping HTML comments. Matches any heading
  // depth (#, ##, ###, …) so a manually-written body isn't penalised for
  // using a different number of hashes than the issue form generates.
  function section(heading) {
    const re = new RegExp(`#+\\s+${heading}\\s*([\\s\\S]*?)(?=\\n#+\\s+|$)`, 'i');
    const m  = body.match(re);
    return m ? m[1].replace(/<!--[\s\S]*?-->/g, '').trim() : '';
  }

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
      if (!section('Install Method')) {
        failures.push('**Install Method** — select how you installed Odysseus');
      }

      if (!section('Operating System')) {
        failures.push('**Operating System** — select your OS');
      }

      const stepsText = section('Steps to Reproduce');
      if (!stepsText || !/\d+\.|[-*]/.test(stepsText)) {
        failures.push('**Steps to Reproduce** — must include at least one numbered or bulleted step');
      }

      if (section('Expected Behaviour').length < 10) {
        failures.push('**Expected Behaviour** — section is empty or too short');
      }

      if (section('Actual Behaviour').length < 10) {
        failures.push('**Actual Behaviour** — section is empty or too short');
      }
      break;
    }

    case 'feature':
      if (!section('Area')) {
        failures.push('**Area** — select which part of the application this affects');
      }

      if (section('Problem or Motivation').length < 20) {
        failures.push(
          '**Problem or Motivation** — section is empty or too short ' +
          '(explain the concrete problem this solves)',
        );
      }

      if (section('Proposed Solution').length < 20) {
        failures.push(
          '**Proposed Solution** — section is empty or too short ' +
          '(describe the change you want to see)',
        );
      }

      if (!section('Are you willing to implement this\\?')) {
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

  async function addLabel(name) {
    if (await labelExists(name)) {
      await github.rest.issues.addLabels({ owner, repo, issue_number: issue.number, labels: [name] });
    } else {
      core.warning(`Label "${name}" does not exist in the repo — skipping. Create it once to enable labelling.`);
    }
  }

  async function dropLabel(name) {
    try {
      await github.rest.issues.removeLabel({ owner, repo, issue_number: issue.number, name });
    } catch (e) {
      if (e.status !== 404 && e.status !== 410) throw e;
    }
  }

  // ── Find existing bot comment to update in-place ──────────────────────────
  const MARKER = '<!-- issue-description-check -->';
  const { data: comments } = await github.rest.issues.listComments({
    owner, repo, issue_number: issue.number,
  });
  const existing = comments.find(c => c.user.type === 'Bot' && c.body.includes(MARKER));

  const LABEL_BAD  = 'needs more info';
  const LABEL_GOOD = 'ready for review';

  if (failures.length === 0) {
    if (existing) {
      await github.rest.issues.deleteComment({ owner, repo, comment_id: existing.id });
    }

    await dropLabel(LABEL_BAD);
    await addLabel(LABEL_GOOD);

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

    if (existing) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body: commentBody });
    } else {
      await github.rest.issues.createComment({ owner, repo, issue_number: issue.number, body: commentBody });
    }

    await dropLabel(LABEL_GOOD);
    await addLabel(LABEL_BAD);

    core.setFailed(`Issue description has ${failures.length} issue(s) — see bot comment for details.`);
  }
};
