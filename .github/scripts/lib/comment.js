// @ts-check
'use strict';

/**
 * Find-by-marker comment upsert/delete for description-check scripts.
 *
 * Extracted from check-pr-description.js and check-issue-description.js (#2453).
 * Both scripts used the same paginated-lookup + marker-based upsert pattern.
 */

/**
 * Find an existing bot comment containing the marker.
 *
 * Uses paginate so repos with 100+ comments on a single issue/PR are
 * fully scanned (the default listComments cap is 30 per page, 100 max).
 *
 * @param {object} github — @octokit/rest instance
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number — PR or issue number
 * @param {string} marker — the HTML comment marker to search for
 * @returns {Promise<object|null>} the existing comment object, or null
 */
async function findComment(github, owner, repo, issue_number, marker) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number, per_page: 100,
  });
  return comments.find(c => (c.body ?? '').includes(marker)) ?? null;
}

/**
 * Upsert a comment: update if it exists (by marker), create if not.
 *
 * @param {object} github
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number
 * @param {string} marker — HTML comment marker used to find the existing comment
 * @param {string} body — the new comment body (should include the marker)
 */
async function upsertComment(github, owner, repo, issue_number, marker, body) {
  const existing = await findComment(github, owner, repo, issue_number, marker);
  if (existing) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number, body });
  }
}

/**
 * Delete a comment by marker, if it exists.
 *
 * @param {object} github
 * @param {string} owner
 * @param {string} repo
 * @param {number} issue_number
 * @param {string} marker
 */
async function deleteComment(github, owner, repo, issue_number, marker) {
  const existing = await findComment(github, owner, repo, issue_number, marker);
  if (existing) {
    await github.rest.issues.deleteComment({ owner, repo, comment_id: existing.id });
  }
}

module.exports = { findComment, upsertComment, deleteComment };
