// @ts-check
'use strict';

/** @param {{ github: import('@octokit/rest').Octokit, context: import('@actions/github').context, core: import('@actions/core') }} */
module.exports = async ({ github, context, core }) => {
  const owner  = context.repo.owner;
  const repo   = context.repo.repo;
  const prNum  = context.payload.pull_request.number;
  const additions = context.payload.pull_request.additions ?? 0;
  const deletions = context.payload.pull_request.deletions ?? 0;
  const delta  = additions + deletions;

  // Size tiers — total lines changed (additions + deletions).
  // Thresholds are intentionally generous: the goal is to help maintainers
  // triage quickly, not to penalise large but necessary PRs.
  const SIZES = [
    { name: 'Size: XS', max:  10, color: '3cbf00', description: 'Tiny change — under 10 lines.' },
    { name: 'Size: S',  max:  50, color: '5d9801', description: 'Small change — 10–50 lines.' },
    { name: 'Size: M',  max: 200, color: 'e6b400', description: 'Medium change — 50–200 lines.' },
    { name: 'Size: L',  max: 500, color: 'eb6420', description: 'Large change — 200–500 lines.' },
    { name: 'Size: XL', max: Infinity, color: 'e11d48', description: 'Very large change — over 500 lines. Consider splitting.' },
  ];

  const target = SIZES.find(s => delta <= s.max) ?? SIZES[SIZES.length - 1];

  // ── Helpers ───────────────────────────────────────────────────────────────

  async function ensureLabel(size) {
    try {
      await github.rest.issues.getLabel({ owner, repo, name: size.name });
    } catch (e) {
      if (e.status === 404) {
        await github.rest.issues.createLabel({
          owner, repo,
          name:        size.name,
          color:       size.color,
          description: size.description,
        });
        core.info(`Created label "${size.name}".`);
      } else {
        throw e;
      }
    }
  }

  async function getCurrentLabels() {
    const { data } = await github.rest.issues.listLabelsOnIssue({
      owner, repo, issue_number: prNum,
    });
    return data.map(l => l.name);
  }

  async function removeLabel(name) {
    try {
      await github.rest.issues.removeLabel({ owner, repo, issue_number: prNum, name });
    } catch (e) {
      if (e.status !== 404 && e.status !== 410) throw e;
    }
  }

  // ── Main ──────────────────────────────────────────────────────────────────

  await ensureLabel(target);

  const current = await getCurrentLabels();
  const sizeNames = SIZES.map(s => s.name);

  // Remove any stale size labels (e.g. PR grew from S to M after a new commit).
  for (const existing of current) {
    if (sizeNames.includes(existing) && existing !== target.name) {
      await removeLabel(existing);
      core.info(`Removed stale label "${existing}" from PR #${prNum}.`);
    }
  }

  // Apply the correct size label if not already present.
  if (!current.includes(target.name)) {
    await github.rest.issues.addLabels({ owner, repo, issue_number: prNum, labels: [target.name] });
  }

  core.info(`PR #${prNum}: ${delta} lines changed → ${target.name}.`);
};
