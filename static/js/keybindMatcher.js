import { IS_MAC, isAltGrEvent } from './platform.js';

function _parseCombo(combo) {
  const raw = String(combo || '').trim().toLowerCase();
  if (!raw) return [];
  const parts = [];
  let token = '';
  for (const ch of raw) {
    if (ch === '+') {
      if (token) {
        parts.push(token);
        token = '';
      } else {
        parts.push('+');
      }
    } else {
      token += ch;
    }
  }
  if (token) parts.push(token);
  return parts.map(part => {
    if (part === 'plus' || part === 'add') return '+';
    if (part === 'minus' || part === 'dash' || part === 'subtract') return '-';
    if (part === 'esc') return 'escape';
    if (part === 'cmd' || part === 'command' || part === 'meta') return 'ctrl';
    return part;
  });
}

export function _eventShortcutKey(e) {
  const key = String(e.key || '').toLowerCase();
  const code = String(e.code || '');
  if (code === 'NumpadAdd' || key === '+') return '+';
  if (code === 'NumpadSubtract' || key === '-' || key === '_' || key === '−') return '-';
  if (code === 'Equal' && key !== '+') return '=';
  if (code === 'Comma') return ',';
  if (code === 'Slash') return '/';
  if (code === 'Space' || key === ' ') return 'space';
  if (key === 'esc') return 'escape';
  return key;
}

function _eventMatchesShortcutKey(e, key) {
  const eventKey = _eventShortcutKey(e);
  const code = String(e.code || '');
  if (key === '+') return eventKey === '+' || eventKey === '=' || code === 'Equal' || code === 'NumpadAdd';
  if (key === '-') return eventKey === '-' || code === 'Minus' || code === 'NumpadSubtract';
  return eventKey === key;
}

export function _matchesCombo(e, combo, isMac = IS_MAC) {
  if (!combo) return false;
  // Drop AltGr keystrokes so typing characters on non-US layouts can't fire a
  // Ctrl+Alt shortcut — e.g. the destructive delete_session. See platform.js.
  if (isAltGrEvent(e, isMac)) return false;
  const parts = _parseCombo(combo);
  const needCtrl = parts.includes('ctrl');
  const needAlt = parts.includes('alt');
  const needShift = parts.includes('shift');
  const key = parts.filter(p => p !== 'ctrl' && p !== 'alt' && p !== 'shift')[0] || '';
  if (needCtrl !== (e.ctrlKey || e.metaKey)) return false;
  if (needAlt !== e.altKey) return false;
  const allowShiftForPlus = key === '+' && !needShift && e.shiftKey && _eventMatchesShortcutKey(e, key);
  if (needShift !== e.shiftKey && !allowShiftForPlus) return false;
  return _eventMatchesShortcutKey(e, key);
}
