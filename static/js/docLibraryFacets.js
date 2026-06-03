// static/js/docLibraryFacets.js
/**
 * Keep the document-library language facet counts in sync when a document is
 * removed locally (optimistic single delete), so the "(N documents)" stat line
 * and the "all (N)" / per-language chips decrease immediately instead of showing
 * a stale count until the next full fetch (#1809).
 *
 * The server's facet aggregation buckets NULL/empty-language and explicit
 * "text" documents together under "text" (see _aggregate_language_facets in
 * routes/document_routes.py), so a removed document's bucket key is
 * `language || "text"`. Returns a new map (does not mutate the input); a count
 * that reaches zero is dropped so its chip disappears.
 */
export function forgetDocLanguage(languages, docLanguage) {
  const key = docLanguage || "text";
  const next = { ...(languages || {}) };
  if (next[key] != null) {
    next[key] = Math.max(0, next[key] - 1);
    if (next[key] === 0) delete next[key];
  }
  return next;
}
