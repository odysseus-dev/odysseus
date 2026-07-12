// @ts-check
'use strict';

/**
 * Fail-soft label management for description-check scripts.
 *
 * These labels are expected to already exist in the repo — managing the
 * repo's label set is the maintainer's job, not the workflow's. We check
 * a label exists before applying it (issues.addLabels would otherwise
 * silently create a missing label) and fail soft — warn and skip — if
 * it's absent.
 *
 * Extracted from check-pr-description.js and check-issue-description.js (#2453).
 */

/**
 * @param {object} github — @octokit/rest instance
 * @param {string} owner
 * @param {string} repo
 * @param {string} name — label name to check
 * @returns {Promise<boolean>}
 */
async function labelExists(github, owner, repo, name) {
  try {
    await github.rest.issues.getLabel({ owner, repo, name });
    return true;
  } catch (e) {
    if (e.status === 404) return false;
    throw e;
  }
}

/**
 * Add a label, but only if it already exists in the repo.
 * Never auto-creates a label — warns and skips if absent.
 *
 * @param {object} github
 * @param {object} core — @actions/core instance (for warnings)
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number
 * @param {string} name — label to add
 */
async function addLabel(github, core, owner, repo, issue_number, name) {
  if (await labelExists(github, owner, repo, name)) {
    try {
      await github.rest.issues.addLabels({ owner, repo, issue_number, labels: [name] });
    } catch (e) {
      // Fail soft on a token that can't write labels so a label permission
      // problem never masks the actual description verdict.
      if (e.status !== 403) throw e;
      core.warning(`Could not add "${name}" — token lacks label write here; skipping.`);
    }
  } else {
    core.warning(`Label "${name}" does not exist in the repo — skipping. Create it once to enable labelling.`);
  }
}

/**
 * Remove a label, ignoring 404/410 (already removed) and 403 (no permission).
 *
 * @param {object} github
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number
 * @param {string} name — label to remove
 */
async function dropLabel(github, owner, repo, issue_number, name) {
  try {
    await github.rest.issues.removeLabel({ owner, repo, issue_number, name });
  } catch (e) {
    if (e.status !== 404 && e.status !== 410 && e.status !== 403) throw e;
  }
}

/**
 * Swap labels: add `add`, remove `remove`.
 *
 * @param {object} github
 * @param {object} core
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number
 * @param {string} add — label to add
 * @param {string} remove — label to remove
 */
async function swapLabel(github, core, owner, repo, issue_number, add, remove) {
  await addLabel(github, core, owner, repo, issue_number, add);
  await dropLabel(github, owner, repo, issue_number, remove);
}

module.exports = { labelExists, addLabel, dropLabel, swapLabel };
