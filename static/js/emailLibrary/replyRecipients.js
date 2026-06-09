// static/js/emailLibrary/replyRecipients.js
//
// Pure helpers for building reply-all recipient lists. No DOM, no fetch,
// no shared state — safe to import anywhere and to unit-test under node.

// Extract the bare email from "Name <email@x>" or a plain "email@x".
export function extractEmail(addr) {
  const m = (addr || '').match(/<([^>]+)>/);
  return (m ? m[1] : (addr || '')).trim().toLowerCase();
}

// Reply-all CC = everyone on the original To + Cc, minus ourselves, with the
// original "Name <email>" form preserved.
//
// `mine` is a single address or a list of the user's own addresses (a
// multi-account user has more than one). Empty/unknown ⇒ no exclusion.
// Comparing by exact extracted email (not a substring `includes`) is what
// fixes issue #360: an empty self address made `"...".includes("")` true for
// every recipient, so reply-all dropped the entire Cc list.
export function buildReplyAllCc(data, mine) {
  const list = Array.isArray(mine) ? mine : [mine];
  const me = new Set(list.map((a) => (a || '').toLowerCase()).filter(Boolean));
  const split = (s) => (typeof s === 'string' ? s : '').split(',').map((x) => x.trim()).filter(Boolean);
  return [...split(data && data.to), ...split(data && data.cc)]
    .filter((addr) => !me.has(extractEmail(addr)))
    .join(', ');
}
