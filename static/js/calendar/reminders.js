// static/js/calendar/reminders.js
//
// Browser-notification poller for calendar reminder notes. Self-contained:
// module-private `_notifFired` Set tracks which note IDs we've already
// notified, persisted to localStorage. Polls `/api/notes?label=calendar`
// every 60 seconds and fires a Notification + toast for any note whose
// `due_date` is in the past but within the staleness window.
//
// `start()` kicks off the poll loop + permission request. Call once from
// the calendar's entry module.

import uiModule from '../ui.js';
import Storage from '../storage.js';

const API_BASE = window.location.origin;

let _notifFired = new Set(Storage.getJSON('cal-notif-fired', []));

// Compute a fresh, system-clock-accurate notification body. Tries the
// note's `event_dtstart` first (set by _createEventReminder); falls back
// to scrubbing stale time tokens out of items[0].text so legacy
// reminders don't show "in 29 min" at 9pm.
function _formatReminderBody(note) {
  const dtstartRaw = note.event_dtstart || note.eventDtstart || null;
  if (dtstartRaw) {
    const start = new Date(dtstartRaw);
    if (!isNaN(start.getTime())) {
      const now = new Date();
      const mins = Math.round((start - now) / 60000);
      const when = start.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      let when2 = '';
      const sameDay = start.toDateString() === now.toDateString();
      if (!sameDay) when2 = ' ' + start.toLocaleDateString([], { month: 'short', day: 'numeric' });
      if (mins >= 1 && mins <= 60) return `Starts in ${mins} min (${when}${when2})`;
      if (mins === 0) return `Starting now (${when}${when2})`;
      if (mins > 60) {
        const h = Math.round(mins / 60);
        return `Starts in ${h} hour${h === 1 ? '' : 's'} (${when}${when2})`;
      }
      if (mins >= -60) return `Started ${Math.abs(mins)} min ago (${when}${when2})`;
      return `Was scheduled for ${when}${when2}`;
    }
  }
  // Legacy notes (no event_dtstart). Scrub stale relative-time strings.
  let body = (note.items || []).map(i => i.text).join('\n') || note.content || '';
  body = body.replace(/\bin\s+\d+\s*(min|minute|hour|hr|day)s?\b/gi, '').trim();
  body = body.replace(/\(\s*\d{1,2}:\d{2}\s*\)/g, '').trim();
  body = body.replace(/\s{2,}/g, ' ');
  return body;
}

// Only fire a reminder if `due` was within this many minutes BEFORE now.
// Stops a fresh browser (empty `cal-notif-fired` localStorage) from spamming
// every 2-week-old reminder on first poll. Anything older is silently
// marked fired so it doesn't keep getting picked up.
const _REMINDER_STALENESS_MIN = 5;

async function _pollReminders() {
  try {
    const res = await fetch(`${API_BASE}/api/notes?label=calendar`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const notes = await res.json();
    const now = new Date();
    const stalenessMs = _REMINDER_STALENESS_MIN * 60 * 1000;
    for (const note of notes) {
      if (!note.due_date || _notifFired.has(note.id)) continue;
      const due = new Date(note.due_date);
      if (isNaN(due)) continue;
      if (due > now) continue; // not yet due
      const ageMs = now - due;
      if (ageMs > stalenessMs) {
        // Too old to fire — mark as seen so we don't recheck every minute.
        _notifFired.add(note.id);
        continue;
      }
      _notifFired.add(note.id);
      const body = _formatReminderBody(note);
      fetch(`${API_BASE}/api/notes/fire-reminder`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          note_id: note.id,
          title: note.title || 'Calendar Reminder',
          body,
        }),
      }).catch(() => {});
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(note.title || 'Calendar Reminder', {
          body,
          icon: '/static/favicon.png',
          tag: `cal-remind-${note.id}`,
        });
      }
      if (uiModule.showToast) uiModule.showToast((note.title || 'Calendar Reminder') + (body ? ' — ' + body : ''));
    }
    // Persist fired set (keep last 200)
    const arr = [..._notifFired].slice(-200);
    localStorage.setItem('cal-notif-fired', JSON.stringify(arr));
  } catch (_) {}
}

let _started = false;

// Idempotent: safe to call multiple times. Kicks off permission request
// and the 60s poll loop on first call.
export function startReminderPoll() {
  if (_started) return;
  _started = true;
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
  _pollReminders();
  setInterval(_pollReminders, 60000);
}
