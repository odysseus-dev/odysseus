// Local-time date and recurrence rules. Callers supply the clock at the original read sites.
export const _ORDINALS = ['1st', '2nd', '3rd', '4th', '5th'];
export const _DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export function _hasTimeComponent(dateStr) {
  return typeof dateStr === 'string' && /T\d{2}:\d{2}/.test(dateStr);
}

export function _formatDueDate(dateStr, clock) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date(clock.now());
  const hasTime = _hasTimeComponent(dateStr);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((due - today) / 86400000);
  const timeStr = hasTime ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
  if (hasTime && d < now) return 'overdue';
  if (!hasTime && diffDays < 0) return 'overdue';
  if (diffDays === 0) return hasTime ? timeStr : 'today';
  if (diffDays === 1) return hasTime ? `tmrw ${timeStr}` : 'tomorrow';
  const dateLabel = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return hasTime ? `${dateLabel} ${timeStr}` : dateLabel;
}

export function _isDueOverdue(dateStr, clock) {
  if (!dateStr) return false;
  const d = new Date(dateStr);
  if (isNaN(d)) return false;
  if (_hasTimeComponent(dateStr)) return d < new Date(clock.now());
  return d < new Date(new Date(clock.now()).toDateString());
}

export function _isDueTodayOrOverdue(dateStr, clock) {
  if (!dateStr) return false;
  const d = new Date(dateStr);
  const now = new Date(clock.now());
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return due <= today;
}

export function _laterTodayDate(clock) {
  const now = new Date(clock.now());
  const eight = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0); // 6pm today
  // If less than 1 hour before 6pm, push to "in 3 hours" instead
  if (eight - now < 60 * 60 * 1000) return new Date(now.getTime() + 3 * 60 * 60 * 1000);
  return eight;
}

export function _tomorrowDate(clock) {
  const t = new Date(clock.now());
  t.setDate(t.getDate() + 1);
  t.setHours(8, 0, 0, 0);
  return t;
}

export function _nextWeekDate(clock) {
  const t = new Date(clock.now());
  const daysUntilMon = (8 - t.getDay()) % 7 || 7;
  t.setDate(t.getDate() + daysUntilMon);
  t.setHours(8, 0, 0, 0);
  return t;
}

export function _toLocalDatetimeStr(d) {
  // Format as YYYY-MM-DDTHH:MM (local, no TZ)
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function _formatReminderTag(dateStr, clock) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date(clock.now());
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (sameDay) return `Today, ${time}`;
  if (isTomorrow) return `Tomorrow, ${time}`;
  const dateLabel = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return `${dateLabel}, ${time}`;
}

export function _nthWeekdayLabel(d) {
  const n = Math.ceil(d.getDate() / 7); // 1..5
  return `${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[d.getDay()]}`;
}

export function _isLastWeekdayOfMonth(d) {
  const test = new Date(d);
  test.setDate(d.getDate() + 7);
  return test.getMonth() !== d.getMonth();
}

export function _nthWeekdayOfMonth(year, month, weekday, n) {
  const first = new Date(year, month, 1);
  const offset = (weekday - first.getDay() + 7) % 7;
  let day = 1 + offset + (n - 1) * 7;
  // Last day of month
  const lastDay = new Date(year, month + 1, 0).getDate();
  if (day > lastDay) day -= 7;
  return new Date(year, month, day, 0, 0, 0);
}

export function _lastWeekdayOfMonth(year, month, weekday) {
  const lastDay = new Date(year, month + 1, 0);
  const back = (lastDay.getDay() - weekday + 7) % 7;
  return new Date(year, month, lastDay.getDate() - back, 0, 0, 0);
}

export function _snapToRepeat(currentDate, normRepeat, clock) {
  const hh = currentDate.getHours();
  const mm = currentDate.getMinutes();
  const now = clock.now();
  const anchor = currentDate.getTime() > now ? currentDate : new Date(clock.now());
  const parts = normRepeat.split(':');
  const kind = parts[0];
  if (kind === 'weekly') {
    const targetWd = parseInt(parts[1], 10);
    if (isNaN(targetWd)) return null;
    const d = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), hh, mm, 0, 0);
    const delta = (targetWd - d.getDay() + 7) % 7;
    d.setDate(d.getDate() + delta);
    if (d.getTime() <= now) d.setDate(d.getDate() + 7);
    return d;
  }
  if (kind === 'monthly') {
    const sub = parts[1];
    let y = anchor.getFullYear();
    let m = anchor.getMonth();
    // Walk forward up to 14 months to find the next matching slot.
    for (let tries = 0; tries < 14; tries++) {
      let target;
      if (sub === 'day') {
        const wantDay = parseInt(parts[2], 10);
        if (isNaN(wantDay)) return null;
        const lastDay = new Date(y, m + 1, 0).getDate();
        target = new Date(y, m, Math.min(wantDay, lastDay));
      } else if (sub === 'nth') {
        const n = parseInt(parts[2], 10);
        const wd = parseInt(parts[3], 10);
        if (isNaN(n) || isNaN(wd)) return null;
        target = _nthWeekdayOfMonth(y, m, wd, n);
      } else if (sub === 'last') {
        const wd = parseInt(parts[2], 10);
        if (isNaN(wd)) return null;
        target = _lastWeekdayOfMonth(y, m, wd);
      } else {
        return null;
      }
      target.setHours(hh, mm, 0, 0);
      if (target.getTime() > now && target.getTime() >= anchor.getTime()) return target;
      m++;
      if (m > 11) { m = 0; y++; }
    }
    return null;
  }
  return null;
}

export function _formatRepeatLabel(repeat, originalDate) {
  if (!repeat || repeat === 'none') return '';
  const norm = _normalizeRepeat(repeat, originalDate);
  if (norm === 'daily') return 'Daily';
  if (norm === 'yearly') return 'Yearly';
  const parts = norm.split(':');
  if (parts[0] === 'weekly') {
    const wd = parseInt(parts[1], 10);
    if (isNaN(wd)) return 'Weekly';
    return `Weekly on ${_DAYS[wd]}s`;
  }
  if (parts[0] === 'monthly') {
    if (parts[1] === 'day') return `Monthly on day ${parts[2]}`;
    if (parts[1] === 'nth') {
      const n = parseInt(parts[2], 10);
      const wd = parseInt(parts[3], 10);
      return `Monthly on ${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[wd]}`;
    }
    if (parts[1] === 'last') {
      const wd = parseInt(parts[2], 10);
      return `Monthly on last ${_DAYS[wd]}`;
    }
  }
  return norm;
}

export function _normalizeRepeat(repeat, originalDate) {
  if (!repeat || repeat === 'none') return 'none';
  if (repeat === 'daily' || repeat === 'yearly') return repeat;
  if (/^(weekly|monthly):/.test(repeat)) return repeat;
  // Legacy bare values: derive params from the original date
  const wd = originalDate.getDay();
  const n = Math.ceil(originalDate.getDate() / 7);
  if (repeat === 'weekly') return `weekly:${wd}`;
  if (repeat === 'monthly') return `monthly:day:${originalDate.getDate()}`;
  if (repeat === 'monthly_nth_weekday') return `monthly:nth:${n}:${wd}`;
  if (repeat === 'monthly_last_weekday') return `monthly:last:${wd}`;
  return repeat;
}

export function _advanceRecurring(dateStr, repeat, clock) {
  const orig = new Date(dateStr);
  const hh = orig.getHours();
  const mm = orig.getMinutes();
  let d = new Date(orig);
  const norm = _normalizeRepeat(repeat, orig);
  if (norm === 'none') return null;

  function step() {
    if (norm === 'daily') {
      d.setDate(d.getDate() + 1);
      return;
    }
    if (norm === 'yearly') {
      d.setFullYear(d.getFullYear() + 1);
      return;
    }
    const parts = norm.split(':');
    const kind = parts[0];
    if (kind === 'weekly') {
      // Snap to the requested weekday in the next 1-7 days
      const targetWd = parseInt(parts[1], 10);
      let delta = (targetWd - d.getDay() + 7) % 7;
      if (delta === 0) delta = 7;
      d.setDate(d.getDate() + delta);
      d.setHours(hh, mm, 0, 0);
      return;
    }
    if (kind === 'monthly') {
      const sub = parts[1];
      const ny = d.getFullYear() + (d.getMonth() === 11 ? 1 : 0);
      const nm = (d.getMonth() + 1) % 12;
      let target;
      if (sub === 'day') {
        const wantDay = parseInt(parts[2], 10);
        const lastDay = new Date(ny, nm + 1, 0).getDate();
        target = new Date(ny, nm, Math.min(wantDay, lastDay));
      } else if (sub === 'nth') {
        const n = parseInt(parts[2], 10);
        const wd = parseInt(parts[3], 10);
        target = _nthWeekdayOfMonth(ny, nm, wd, n);
      } else if (sub === 'last') {
        const wd = parseInt(parts[2], 10);
        target = _lastWeekdayOfMonth(ny, nm, wd);
      } else {
        d = null; return;
      }
      target.setHours(hh, mm, 0, 0);
      d = target;
      return;
    }
    d = null;
  }

  step();
  if (d === null) return null;
  const now = clock.now();
  // Cap catch-up to avoid runaway on a malformed/very-old date.
  let guard = 5000;
  while (d.getTime() <= now) {
    if (--guard <= 0) return null;
    step();
    if (d === null) return null;
  }
  return _toLocalDatetimeStr(d);
}
