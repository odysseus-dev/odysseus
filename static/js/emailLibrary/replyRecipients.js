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
// `myAddress` empty/unknown ⇒ no exclusion. Comparing by exact extracted email
// (not a substring `includes`) is what fixes issue #360: an empty self address
// made `"...".includes("")` true for every recipient, so reply-all dropped the
// entire Cc list and kept only the original sender.
export function buildReplyAllCc(data, myAddress) {
  const me = (myAddress || '').toLowerCase();
  const split = (s) => (s || '').split(',').map((x) => x.trim()).filter(Boolean);
  return [...split(data && data.to), ...split(data && data.cc)]
    .filter((addr) => !me || extractEmail(addr) !== me)
    .join(', ');
}
