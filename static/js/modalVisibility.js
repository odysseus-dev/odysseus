// Own both completion paths of an animated close so neither can outlive it.
const _pendingCloses = new WeakMap();

function cancelClose(modal) {
  const pending = _pendingCloses.get(modal);
  if (!pending) return;
  clearTimeout(pending.timer);
  pending.content.removeEventListener('animationend', pending.onEnd);
  pending.content.classList.remove('modal-closing');
  _pendingCloses.delete(modal);
}

export function showModal(modal) {
  if (!modal) return;
  cancelClose(modal);
  modal.classList.remove('hidden');
}

export function hideModal(modal) {
  if (!modal) return;
  cancelClose(modal);
  const content = modal.querySelector('.modal-content, .settings-modal-content');
  // A minimized window has no exit animation. Registering a listener here
  // would let its next entrance animation close the newly opened window.
  if (!content || modal.classList.contains('hidden')) {
    modal.classList.add('hidden');
    return;
  }
  const pending = { content, timer: null, onEnd: null };
  const finish = () => {
    if (_pendingCloses.get(modal) !== pending) return;
    cancelClose(modal);
    modal.classList.add('hidden');
  };
  pending.onEnd = event => {
    if (event.target === content) finish();
  };
  _pendingCloses.set(modal, pending);
  content.classList.add('modal-closing');
  content.addEventListener('animationend', pending.onEnd);
  pending.timer = setTimeout(finish, 250);
}
