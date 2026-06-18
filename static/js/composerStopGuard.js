/**
 * Decide whether activating the shared send/stop button should be handled as a
 * STOP (abort the in-flight run) rather than a SEND (submit composer text).
 *
 * The chat composer uses ONE `<button type="submit">` for both Send and Stop.
 * Historically `handleChatSubmit` discriminated the two purely off the
 * module-level `isStreaming` flag. But the button can visually show the Stop
 * icon while `isStreaming` is still false — a state desync, most notably a run
 * that was resumed after a page refresh, before the resume reader re-arms the
 * flag. In that window a click fell through to the SEND path, which clears the
 * composer (`messageInput.value = ''`) and fires the user's freshly typed draft
 * as a brand-new message — silently destroying what they had typed.
 *
 * Treat the activation as Stop whenever EITHER signal says a run is active:
 *   - the streaming flag is set, OR
 *   - the button is visually in its streaming/stop mode (`dataset.mode`).
 *
 * Keying off the button's visible mode (not just the flag) guarantees that a
 * click on something showing a Stop icon is always handled as a stop, so the
 * typed draft is preserved instead of being cleared and sent.
 *
 * @param {boolean} isStreaming               module-level streaming flag
 * @param {{ dataset?: { mode?: string } } | null} [submitBtn]  the send/stop button element
 * @returns {boolean} true → handle as Stop (abort, keep draft); false → proceed to send
 */
export function shouldTreatStopClick(isStreaming, submitBtn) {
  if (isStreaming) return true;
  const mode = submitBtn && submitBtn.dataset ? submitBtn.dataset.mode : '';
  return mode === 'streaming';
}

export default { shouldTreatStopClick };
