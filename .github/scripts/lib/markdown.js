// @ts-check
'use strict';

/**
 * Shared markdown helpers for description-check scripts.
 *
 * Extracted from check-pr-description.js and check-issue-description.js (#2453)
 * so the two scripts (and future CI checks) share a single source of truth.
 */

/**
 * Strip HTML comments so placeholder text inside <!-- --> doesn't count as content.
 * @param {string} text
 * @returns {string}
 */
function strip(text) {
  return (text ?? '').replace(/<!--[\s\S]*?-->/g, '').trim();
}

/**
 * Extract the text content of a markdown Section.
 *
 * Matches any heading depth (#, ##, ###, …) so the check doesn't break if
 * the template's heading level changes. Escapes regex specials in the
 * heading internally so callers pass plain text (e.g. `Are you willing to
 * implement this?` without pre-escaping the `?`).
 *
 * @param {string} body  — the full issue/PR body
 * @param {string} heading — the heading text to match (case-insensitive)
 * @returns {string} the section's text content, stripped of HTML comments
 */
function section(body, heading) {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`#+\\s+${escaped}[\\s\\S]*?(?=\\n#+\\s+|$)`, 'i');
  const m = (body ?? '').match(re);
  return strip(m?.[0].replace(new RegExp(`#+\\s+${escaped}`, 'i'), '') ?? '');
}

module.exports = { strip, section };
