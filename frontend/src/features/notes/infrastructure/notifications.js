/** Browser notification permission/display port, independent of Notes rendering. */
export function createNotesNotifications({ browser, notify, openPanel, toast }) {
  async function ensurePermission() {
    if (!('Notification' in browser)) return false;
    if (notify.permission === 'granted') return true;
    if (notify.permission === 'denied') return false;
    try { return await notify.requestPermission() === 'granted'; }
    catch { return false; }
  }
  function show(title, body, noteId) {
    if ('Notification' in browser && notify.permission === 'granted') {
      try {
        const notification = new notify(title, { body, tag: 'note-' + noteId, icon: '/static/favicon.ico' });
        notification.onclick = () => { browser.focus(); openPanel(); notification.close(); };
      } catch {}
    }
    toast(title);
  }
  return { ensurePermission, show };
}
