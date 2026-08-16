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

  const descriptionProblems = [];

  // 1. Summary must be filled in.
  if (section('Summary').length < 20) {
    descriptionProblems.push('**Summary** is empty or too short — describe what changed and why.');
  }

  // 2. Linked Issue must reference a real issue. Accept a bare #NNN, a closing
  //    keyword + #NNN, or a full issue URL (e.g. .../issues/123) — the strict
  //    keyword-prefixed form previously false-flagged correctly-linked PRs.
  const linkedSection = section('Linked Issue');
  const hasIssueRef = /#\d+\b/.test(linkedSection) || /\/issues\/\d+/.test(linkedSection);
  if (!linkedSection || !hasIssueRef) {
    descriptionProblems.push('**Linked Issue** — add a reference like `Fixes #NNN`, a bare `#NNN`, or a link to the issue.');
  }

  // 3. At least one Type of Change box must be checked.
  const typeBlock = body.match(/##\s+Type of Change[\s\S]*?(?=\n##\s|$)/i)?.[0] ?? '';
  if (!/- \[x\]/i.test(typeBlock)) {
    descriptionProblems.push('**Type of Change** — check at least one box.');
  }

  // 4. Duplicate-search checklist item must be checked.
  if (!/- \[x\] I searched/i.test(body)) {
    descriptionProblems.push('**Checklist** — check the duplicate-search box to confirm you searched existing issues and PRs.');
  }

  // 5. How to Test must contain enough real detail for a reviewer to act on.
  //    Any format is fine — numbered steps, prose, the commands you ran, or a
  //    code block — so we only require non-trivial content, not a specific shape.
  const howTo = section('How to Test');
  if (howTo.length < 30) {
    descriptionProblems.push('**How to Test** — explain how a reviewer can verify this change. Numbered steps, the commands you ran, or a short code block all work — give a sentence or two of real detail (not just "tested locally").');
  }

  // Classify paths from GitHub's API. This workflow runs in the privileged base
  // context, so it must never check out or execute code from the PR branch.
  const changedFiles = await github.paginate(github.rest.pulls.listFiles, {
    owner, repo, pull_number: prNum, per_page: 100,
  });
  const changedPaths = changedFiles.map(file => file.filename);

  function isUiSensitivePath(filename) {
    const path = filename.toLowerCase();
    return path.startsWith('static/')
      || path.startsWith('templates/')
      || /\.(?:html?|css|svg)$/.test(path);
  }

  function isDocsOnlyPath(filename) {
    const path = filename.toLowerCase();
    return /\.(?:md|mdx|rst|adoc|txt)$/.test(path)
      || (path.startsWith('docs/') && !isUiSensitivePath(path));
  }

  function isRuntimeSensitivePath(filename) {
    const path = filename.toLowerCase();
    if (isUiSensitivePath(path)) return false;
    if (path.startsWith('tests/') || path.startsWith('.github/')) return false;
    return /^(?:app\.py|routes\/|services\/|src\/|core\/|mcp_servers\/|scripts\/|docker\/)/.test(path)
      || /^(?:dockerfile|docker-compose.*\.ya?ml|requirements(?:-optional)?\.txt|pyproject\.toml|setup\.py)$/.test(path)
      || /\.(?:py|sh|ps1|bat)$/.test(path);
  }

  let classification = 'tooling';
  if (changedPaths.some(isUiSensitivePath)) {
    classification = 'UI-sensitive';
  } else if (changedPaths.some(isRuntimeSensitivePath)) {
    classification = 'backend/runtime';
  } else if (changedPaths.length > 0 && changedPaths.every(isDocsOnlyPath)) {
    classification = 'docs-only';
  }

  const appRan = /- \[x\]\s+I actually ran the app\b/i.test(body);
  const appNotRun = /- \[x\]\s+I did not run the app\/runtime validation\b/i.test(body);
  // Anchor on the wording, not the template's emphasis: a ticked box the author
  // retyped without the surrounding ** renders identically on the PR page, so
  // treating it as unchecked is invisible from their side. Matches the two
  // attestations above, which already ignore formatting.
  const screenshotChecked = /- \[x\]\s+[*_]{0,2}Screenshot or short clip[*_]{0,2}/i.test(body);
  const screenshotSection = section('Screenshots / clips');
  const hasVisualEvidence = /!\[[^\]]*\]\([^)]+\)|<(?:img|video|source)\b[^>]*(?:src|href)=|https?:\/\/[^\s)]+/i.test(screenshotSection);
  const evidenceGaps = [];
  let needsRuntimeValidation = false;
  let needsVisualEvidence = false;

  if (classification === 'backend/runtime' || classification === 'UI-sensitive') {
    if (appRan && appNotRun) {
      needsRuntimeValidation = true;
      evidenceGaps.push('The app-run and explicit not-run boxes are both checked. Select the one state that is true.');
    } else if (!appRan) {
      needsRuntimeValidation = true;
      if (appNotRun) {
        evidenceGaps.push('The author explicitly reports that app/runtime validation was not performed.');
      } else {
        evidenceGaps.push('App/runtime validation is not author-attested. Check the run box only after running it, or check the explicit not-run box and describe the gap.');
      }
    }
  }

  if (classification === 'UI-sensitive') {
    if (!screenshotChecked) {
      needsVisualEvidence = true;
      evidenceGaps.push('The screenshot/clip checkbox is not checked for this UI-sensitive change.');
    }
    if (!hasVisualEvidence) {
      needsVisualEvidence = true;
      evidenceGaps.push('The Screenshots / clips section does not contain an actual attachment or link.');
    }
  }

  // ── Comment ──────────────────────────────────────────────────────────────
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: prNum, per_page: 100,
  });
  const existing = comments.find(c => (c.body ?? '').includes(MARKER));

  if (descriptionProblems.length === 0 && evidenceGaps.length === 0) {
    if (existing) {
      await github.rest.issues.deleteComment({ owner, repo, comment_id: existing.id });
    }
  } else {
    const commentLines = [MARKER];
    if (descriptionProblems.length > 0) {
      commentLines.push(
        '⚠️ **PR description — action needed**',
        '',
        'The following required sections are missing or incomplete. Please update the PR description to address them:',
        '',
        descriptionProblems.map(problem => `- ${problem}`).join('\n'),
      );
    } else {
      commentLines.push(
        '⚠️ **PR description is complete; validation evidence is still outstanding**',
        '',
        `Changed-file classification: **${classification}**.`,
      );
    }
    if (evidenceGaps.length > 0) {
      commentLines.push(
        '',
        '**Author-reported runtime / visual state**',
        '',
        evidenceGaps.map(gap => `- ${gap}`).join('\n'),
        '',
        'Checkboxes are author attestations. GitHub Actions results remain the execution evidence for CI; this check does not prove that a local command ran.',
      );
    }
    commentLines.push(
      '',
      '---',
      '_This comment updates automatically when the description or changed files change._',
    );
    const commentBody = commentLines.join('\n');

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
      if (e.status === 403) {
        core.warning(`Could not inspect label "${name}" — token lacks label read access; skipping.`);
        return false;
      }
      throw e;
    }
  }

  async function setLabel(name, wanted) {
    if (wanted && await labelExists(name)) {
      try {
        await github.rest.issues.addLabels({ owner, repo, issue_number: prNum, labels: [name] });
      } catch (e) {
        // Fail soft on a token that can't write labels so a label permission
        // problem never masks the actual description verdict.
        if (e.status !== 403 && e.status !== 404) throw e;
        core.warning(`Could not add "${name}" — label is unavailable or the token lacks label write access; skipping.`);
      }
    } else if (wanted) {
      core.warning(`Label "${name}" does not exist in the repo — skipping. Create it once to enable labelling.`);
    } else {
      try {
        await github.rest.issues.removeLabel({ owner, repo, issue_number: prNum, name });
      } catch (e) {
        if (e.status !== 404 && e.status !== 410 && e.status !== 403) throw e;
      }
    }
  }

  const descriptionComplete = descriptionProblems.length === 0;
  const evidenceComplete = evidenceGaps.length === 0;
  const isDraft = Boolean(context.payload.pull_request.draft);
  await setLabel(
    'ready for review',
    descriptionComplete && evidenceComplete && !isDraft,
  );
  await setLabel('needs work', !descriptionComplete);
  await setLabel('needs runtime validation', needsRuntimeValidation);
  await setLabel('needs visual evidence', needsVisualEvidence);

  if (!descriptionComplete) {
    core.setFailed(`PR description has ${descriptionProblems.length} issue(s) — see bot comment for details.`);
  }
};
