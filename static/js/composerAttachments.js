// Whether the composer has something to send besides text: files still being
// uploaded, or photos that Resend/Edit queued by id from the original message.
// A photo-only Resend/Edit clicks Send with the box empty, so without the
// queued ids the Send button reads as "New chat" and opens one instead.
export function composerHasAttachments(pendingUploadCount, queuedAttachmentIds) {
  if (Number(pendingUploadCount) > 0) return true;
  return Array.isArray(queuedAttachmentIds) && queuedAttachmentIds.length > 0;
}
