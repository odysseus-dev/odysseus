// static/js/uploadFeedback.js
/**
 * How one chat upload rejection is reported to the user (issue #6235).
 *
 * Before this module the same failure was announced twice from two independent
 * places: `fileHandler.js` toasted the server's reason in the attachment strip
 * while `chat.js` unconditionally showed the generic "Upload failed. Attachment
 * kept so you can retry." in the composer. One rejection therefore produced two
 * overlapping errors, and the useful one (the reason) landed in the spot users
 * do not read first.
 *
 * The fix is a single owner: `fileHandler.uploadPending()` now *records* the
 * reason instead of announcing it, and `chat.js` renders exactly one composer
 * message built here. Keeping the formatting in a dependency-free module means
 * the messaging rules are testable in node without a DOM, and neither side can
 * drift back to its own wording.
 */

/**
 * Pull the human-readable reason out of a failed `/api/upload` response.
 *
 * FastAPI detail shapes this has to survive, all of which are live in this repo:
 *   - `detail` a string           (`/api/upload` -> UploadHandler)
 *   - `detail` `{error, message}` (`validate_file_upload`)
 *   - `detail` a list of `{msg}`  (request-validation errors)
 *   - `{error: "..."}` / `{message: "..."}` at the top level
 *
 * Falls back to the HTTP status so the caller always has something concrete,
 * never an empty string and never "[object Object]".
 *
 * @param {*} payload Parsed JSON body, or anything else when parsing failed.
 * @param {number} [status] HTTP status of the response, when there was one.
 * @returns {string} A non-empty, user-safe reason.
 */
export function extractUploadErrorReason(payload, status) {
  const text = (value) => (typeof value === 'string' ? value.trim() : '');

  if (payload && typeof payload === 'object') {
    const detail = payload.detail;
    const direct = text(detail);
    if (direct) return direct;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const nested = text(detail.message) || text(detail.error);
      if (nested) return nested;
    }
    if (Array.isArray(detail)) {
      for (const item of detail) {
        const msg = item && typeof item === 'object' ? text(item.msg) : '';
        if (msg) return msg;
      }
    }
    const top = text(payload.error) || text(payload.message);
    if (top) return top;
  }

  return status ? `HTTP ${status}` : 'network error';
}

/**
 * Compose the single user-facing message for a failed upload.
 *
 * One sentence carrying the reason, one sentence saying the attachment was
 * not dropped — the two facts the reporter of #6235 had to read in two
 * different corners of the screen.
 *
 * @param {string} reason Reason from {@link extractUploadErrorReason}.
 * @returns {string}
 */
export function formatUploadFailure(reason) {
  const why = (reason === null || reason === undefined ? '' : String(reason))
    .trim()
    // A server reason that already reads "Upload failed: …" must not be
    // prefixed twice.
    .replace(/^upload failed\s*:?\s*/i, '');
  const head = why ? `Upload failed: ${why}` : 'Upload failed';
  const punctuated = /[.!?]$/.test(head) ? head : `${head}.`;
  return `${punctuated} Attachment kept so you can retry.`;
}

export default { extractUploadErrorReason, formatUploadFailure };
