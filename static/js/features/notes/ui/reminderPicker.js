/** Reminder date presets and recurrence picker interactions. */
import { _formatRepeatLabel, _toLocalDatetimeStr, _normalizeRepeat, _DAYS, _ORDINALS } from '../domain/recurrence.js';
export function createReminderPicker({
  lifecycle,
  _ensureNotificationPermission,
  _esc,
  _formatReminderTag,
  _laterTodayDate,
  _nextWeekDate,
  _snapToRepeat,
  _tomorrowDate,
  bindMenuDismiss,
  dismissOrRemove
}) {
  function _wireReminderPicker(form) {
    // Reminder bell : opens dropdown menu
    const remindBtn = form.querySelector('.note-form-remind-btn');
    const dueInput = form.querySelector('.note-form-due');
    const repeatInput = form.querySelector('.note-form-repeat');
    const tagsEl = form.querySelector('.note-form-reminder-tags');

    function _renderReminderTag() {
      if (!tagsEl) return;
      const v = dueInput.value;
      const rep = repeatInput.value || 'none';
      if (!v) { tagsEl.innerHTML = ''; return; }
      const label = _formatReminderTag(v);
      const repLabel = rep !== 'none' ? ` · ${_formatRepeatLabel(rep, new Date(v))}` : '';
      tagsEl.innerHTML = `<button class="note-reminder-tag" type="button" title="Edit reminder">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
      <span>${_esc(label)}${_esc(repLabel)}</span>
      <span class="note-reminder-tag-x" title="Remove">×</span>
    </button>`;
      tagsEl.querySelector('.note-reminder-tag').addEventListener('click', (e) => {
        if (e.target.classList.contains('note-reminder-tag-x')) {
          dueInput.value = '';
          repeatInput.value = 'none';
          _renderReminderTag();
          return;
        }
        _openReminderMenu(remindBtn || tagsEl, true);
      });
    }

    function _openReminderMenu(anchor, isEdit = false) {
      // Close any existing menu
      document.querySelectorAll('.note-reminder-menu').forEach(m => m.remove());
      const menu = document.createElement('div');
      menu.className = 'note-reminder-menu';
      document.body.appendChild(menu);

      const presetItems = [
        { label: 'Later today', sub: _laterTodayDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_laterTodayDate())) },
        { label: 'Tomorrow', sub: _tomorrowDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_tomorrowDate())) },
        { label: 'Next week', sub: _nextWeekDate().toLocaleDateString([], { weekday: 'short' }) + ' ' + _nextWeekDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_nextWeekDate())) },
        { label: 'Select date and time', sub: '', action: () => _pickCustomDate() },
      ];

      // Sub-page state for the repeat picker. null = top page.
      // 'weekly' | 'monthly' | 'monthly_nth'
      let subMode = null;
      // Temporary state for monthly_nth so user can click N then weekday (or vice versa)
      // before committing.
      let nthDraft = { n: 0, w: -1 };

      const DAY_SHORT = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

      function getNorm() {
        if (!dueInput.value) return 'none';
        return _normalizeRepeat(repeatInput.value || 'none', new Date(dueInput.value));
      }

      function commit(val) {
        repeatInput.value = val;
        _renderReminderTag();
        menu.remove();
      }

      // Like commit, but first snaps dueInput.value forward to the next matching
      // slot for the chosen recurrence. Use for weekly/monthly variants where the
      // current due date may not match the chosen pattern (e.g. user picks
      // "weekly on Mondays" while the date is a Wednesday).
      function snapAndCommit(val) {
        if (dueInput.value) {
          const cur = new Date(dueInput.value);
          const norm = _normalizeRepeat(val, cur);
          const snapped = _snapToRepeat(cur, norm);
          if (snapped) {
            dueInput.value = _toLocalDatetimeStr(snapped);
            if (remindBtn) remindBtn.classList.add('has-date');
          }
        }
        commit(val);
      }

      function reposition() {
        const rect = anchor.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const mw = menu.offsetWidth || 220;
        const mh = menu.offsetHeight || 280;
        let top = rect.bottom + 4;
        let left = rect.left;
        if (top + mh > vh - 8) top = Math.max(8, rect.top - mh - 4);
        if (left + mw > vw - 8) left = Math.max(8, vw - mw - 8);
        if (left < 8) left = 8;
        menu.style.top = top + 'px';
        menu.style.left = left + 'px';
      }

      function render() {
        let html = '';

        if (subMode === null) {
          html += '<div class="note-reminder-menu-title">Remind me later</div>';
          for (let i = 0; i < presetItems.length; i++) {
            const it = presetItems[i];
            html += `<button class="note-reminder-menu-item" data-action="preset" data-i="${i}"><span>${it.label}</span><span class="note-reminder-menu-sub">${it.sub}</span></button>`;
          }
          if (isEdit && dueInput.value) {
            const norm = getNorm();
            html += '<div class="note-reminder-menu-divider"></div>';
            html += '<div class="note-reminder-menu-title">Repeat</div>';
            // None
            html += `<button class="note-reminder-menu-item${norm === 'none' ? ' active' : ''}" data-action="set" data-val="none"><span>Doesn't repeat</span>${norm === 'none' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
            // Daily
            html += `<button class="note-reminder-menu-item${norm === 'daily' ? ' active' : ''}" data-action="set" data-val="daily"><span>Daily</span>${norm === 'daily' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
            // Weekly →
            {
              const isW = norm.startsWith('weekly:');
              const wd = isW ? parseInt(norm.split(':')[1], 10) : null;
              const sub = isW && !isNaN(wd) ? `<span class="note-reminder-menu-sub">${_DAYS[wd]}</span>` : '';
              html += `<button class="note-reminder-menu-item${isW ? ' active' : ''}" data-action="sub" data-sub="weekly"><span>Weekly</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
            }
            // Monthly →
            {
              const isM = norm.startsWith('monthly:');
              const sub = isM ? `<span class="note-reminder-menu-sub">${_monthlyShortDescriptor(norm)}</span>` : '';
              html += `<button class="note-reminder-menu-item${isM ? ' active' : ''}" data-action="sub" data-sub="monthly"><span>Monthly</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
            }
            // Yearly
            html += `<button class="note-reminder-menu-item${norm === 'yearly' ? ' active' : ''}" data-action="set" data-val="yearly"><span>Yearly</span>${norm === 'yearly' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
          }
        } else if (subMode === 'weekly') {
          const norm = getNorm();
          const curWd = norm.startsWith('weekly:') ? parseInt(norm.split(':')[1], 10) : -1;
          html += `<button class="note-reminder-menu-back" data-action="back"><span class="note-reminder-menu-arrow-back">‹</span> Repeat</button>`;
          html += '<div class="note-reminder-menu-title">Weekly on…</div>';
          html += '<div class="note-reminder-weekday-row">';
          for (let i = 0; i < 7; i++) {
            html += `<button class="note-reminder-day-chip${curWd === i ? ' active' : ''}" data-action="weekly-pick" data-wd="${i}" title="${_DAYS[i]}">${DAY_SHORT[i]}</button>`;
          }
          html += '</div>';
        } else if (subMode === 'monthly') {
          const norm = getNorm();
          const dueDate = new Date(dueInput.value);
          const dayN = dueDate.getDate();
          html += `<button class="note-reminder-menu-back" data-action="back"><span class="note-reminder-menu-arrow-back">‹</span> Repeat</button>`;
          html += '<div class="note-reminder-menu-title">Monthly on…</div>';
          // Day N : uses the chosen date's day. Always offered.
          const dayVal = `monthly:day:${dayN}`;
          html += `<button class="note-reminder-menu-item${norm === dayVal ? ' active' : ''}" data-action="set" data-val="${dayVal}"><span>Day ${dayN} every month</span>${norm === dayVal ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
          // Nth weekday →
          {
            const isNth = norm.startsWith('monthly:nth:');
            const sub = isNth ? `<span class="note-reminder-menu-sub">${_monthlyShortDescriptor(norm)}</span>` : '';
            html += `<button class="note-reminder-menu-item${isNth ? ' active' : ''}" data-action="sub" data-sub="monthly_nth"><span>Nth weekday</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
          }
        } else if (subMode === 'monthly_nth') {
          // Pick ordinal (1..4) and weekday (0..6); commit when both chosen.
          html += `<button class="note-reminder-menu-back" data-action="back-monthly"><span class="note-reminder-menu-arrow-back">‹</span> Monthly</button>`;
          html += '<div class="note-reminder-menu-title">Nth weekday of month</div>';
          html += '<div class="note-reminder-menu-sublabel">Which one</div>';
          html += '<div class="note-reminder-weekday-row">';
          for (let i = 1; i <= 4; i++) {
            html += `<button class="note-reminder-day-chip wide${nthDraft.n === i ? ' active' : ''}" data-action="nth-n" data-n="${i}">${_ORDINALS[i - 1]}</button>`;
          }
          html += '</div>';
          html += '<div class="note-reminder-menu-sublabel">Weekday</div>';
          html += '<div class="note-reminder-weekday-row">';
          for (let i = 0; i < 7; i++) {
            html += `<button class="note-reminder-day-chip${nthDraft.w === i ? ' active' : ''}" data-action="nth-w" data-wd="${i}" title="${_DAYS[i]}">${DAY_SHORT[i]}</button>`;
          }
          html += '</div>';
          html += '<div class="note-reminder-menu-divider"></div>';
          const ready = nthDraft.n > 0 && nthDraft.w >= 0;
          const lbl = ready ? `Save: ${_ORDINALS[nthDraft.n - 1]} ${_DAYS[nthDraft.w]}` : 'Pick week and weekday';
          html += `<button class="note-reminder-menu-item note-reminder-menu-confirm${ready ? '' : ' disabled'}" data-action="nth-save" ${ready ? '' : 'disabled'}><span>${lbl}</span></button>`;
        }

        menu.innerHTML = html;
        reposition();
        wire();
      }

      function wire() {
        menu.querySelectorAll('[data-action]').forEach(el => {
          el.addEventListener('click', (e) => {
            e.stopPropagation();
            const a = el.dataset.action;
            if (a === 'preset') {
              const it = presetItems[parseInt(el.dataset.i, 10)];
              it.action();
              menu.remove();
            } else if (a === 'set') {
              snapAndCommit(el.dataset.val);
            } else if (a === 'sub') {
              subMode = el.dataset.sub;
              // Seed nth draft from saved value only on first entry : preserve
              // in-progress picks across back-trips (Nth → back → Nth again).
              if (subMode === 'monthly_nth' && nthDraft.n === 0 && nthDraft.w === -1) {
                const norm = getNorm();
                const m = norm.match(/^monthly:nth:(\d):(\d)$/);
                if (m) nthDraft = { n: parseInt(m[1], 10), w: parseInt(m[2], 10) };
              }
              render();
            } else if (a === 'back') {
              subMode = null;
              render();
            } else if (a === 'back-monthly') {
              subMode = 'monthly';
              render();
            } else if (a === 'weekly-pick') {
              snapAndCommit(`weekly:${el.dataset.wd}`);
            } else if (a === 'nth-n') {
              nthDraft.n = parseInt(el.dataset.n, 10);
              render();
            } else if (a === 'nth-w') {
              nthDraft.w = parseInt(el.dataset.wd, 10);
              render();
            } else if (a === 'nth-save') {
              if (nthDraft.n > 0 && nthDraft.w >= 0) {
                snapAndCommit(`monthly:nth:${nthDraft.n}:${nthDraft.w}`);
              }
            }
          });
        });
      }

      render();
      // Click outside to close (single global handler attached after first paint)
      setTimeout(() => {
        const close = (e) => {
          if (!menu.isConnected) { document.removeEventListener('click', close); return; }
          if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); }
        };
        if (menu.isConnected) lifecycle.listen(menu, document, 'click', close);
      }, 0);
    }

    function _monthlyShortDescriptor(norm) {
      const parts = norm.split(':');
      if (parts[1] === 'day') return `Day ${parts[2]}`;
      if (parts[1] === 'nth') {
        const n = parseInt(parts[2], 10);
        const wd = parseInt(parts[3], 10);
        return `${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[wd].slice(0, 3)}`;
      }
      if (parts[1] === 'last') {
        const wd = parseInt(parts[2], 10);
        return `Last ${_DAYS[wd].slice(0, 3)}`;
      }
      return '';
    }

    function _setReminder(datetimeLocalStr) {
      dueInput.value = datetimeLocalStr;
      if (remindBtn) {
        remindBtn.classList.add('has-date');
        // Jingle the bell. CSS handles the animation; remove + reflow + re-add
        // so it replays every time the user sets/changes a reminder.
        const _bell = remindBtn.querySelector('svg');
        if (_bell) {
          _bell.classList.remove('jingling');
          void _bell.offsetWidth;
          _bell.classList.add('jingling');
          setTimeout(() => _bell.classList.remove('jingling'), 700);
        }
      }
      _renderReminderTag();
      _ensureNotificationPermission();
    }

    function _pickCustomDate() {
      // Replace the dropdown menu with a small inline picker
      document.querySelectorAll('.note-reminder-menu').forEach(dismissOrRemove);
      const menu = document.createElement('div');
      menu.className = 'note-reminder-menu';
      const initial = dueInput.value || _toLocalDatetimeStr(_tomorrowDate());
      menu.innerHTML = `
      <div class="note-reminder-menu-title">Pick date and time</div>
      <div class="note-reminder-menu-picker">
        <input type="datetime-local" class="note-reminder-date-input" />
      </div>
      <div class="note-reminder-menu-divider"></div>
      <button class="note-reminder-menu-item note-reminder-menu-confirm">
        <span>Save</span>
      </button>
    `;
      document.body.appendChild(menu);
      // Position next to the bell button
      const anchor = remindBtn || form.querySelector('.note-form-reminder-tags');
      const rect = anchor.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const mw = menu.offsetWidth || 240;
      const mh = menu.offsetHeight || 200;
      let top = rect.bottom + 4;
      let left = rect.left;
      if (top + mh > vh - 8) top = Math.max(8, rect.top - mh - 4);
      if (left + mw > vw - 8) left = Math.max(8, vw - mw - 8);
      if (left < 8) left = 8;
      menu.style.top = top + 'px';
      menu.style.left = left + 'px';
      const dInput = menu.querySelector('.note-reminder-date-input');
      dInput.value = initial;
      dInput.focus();
      if (typeof dInput.showPicker === 'function') {
        try { dInput.showPicker(); } catch {}
      }
      const close = bindMenuDismiss(menu, () => { menu.remove(); });
      menu.querySelector('.note-reminder-menu-confirm').addEventListener('click', () => {
        if (dInput.value) _setReminder(dInput.value);
        close();
      });
    }

    if (remindBtn) remindBtn.addEventListener('click', (e) => { e.stopPropagation(); _openReminderMenu(remindBtn, !!dueInput.value); });
    _renderReminderTag();


  }

  return { _wireReminderPicker };
}
