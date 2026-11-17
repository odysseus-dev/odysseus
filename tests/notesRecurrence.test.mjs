import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as rules from '../frontend/src/features/notes/domain/recurrence.js';

const baseline = fs.readFileSync(new URL('./fixtures/notesRecurrenceBaseline.mjs', import.meta.url), 'utf8');
const timed = new Set(['_formatDueDate', '_isDueOverdue', '_isDueTodayOrOverdue', '_laterTodayDate', '_tomorrowDate', '_nextWeekDate', '_formatReminderTag', '_snapToRepeat', '_advanceRecurring']);
for (const zone of ['UTC', 'Europe/Lisbon', 'America/New_York', 'Australia/Sydney']) {
  test(`recurrence differential oracle in ${zone}`, () => {
    const oldZone = process.env.TZ;
    process.env.TZ = zone;
    try {
      const now = Date.parse('2024-03-10T01:59:00');
      class FixedDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
      const original = vm.createContext({ Date: FixedDate });
      vm.runInContext(baseline, original);
      const compare = (name, args) => {
        const before = original[name](...args);
        const after = rules[name](...args, ...(timed.has(name) ? [{ now: () => now }] : []));
        assert.equal(after instanceof Date ? after.getTime() : after, before instanceof Date ? before.getTime() : before, `${name}(${args})`);
      };
      const dates = ['2024-01-31T09:17', '2024-02-29T09:17', '2023-02-28T09:17', '2024-03-09T02:30', '2024-11-02T01:30', '2030-07-31T18:42', '1900-01-01T09:00', 'invalid', '2024-03-10'];
      const repeats = [null, '', 'none', 'daily', 'weekly', 'monthly', 'yearly', 'monthly_nth_weekday', 'monthly_last_weekday', 'weekly:1', 'weekly:bad', 'monthly:day:31', 'monthly:nth:5:1', 'monthly:last:0', 'monthly:bad', 'unknown'];
      for (const date of dates) {
        for (const repeat of repeats) {
          compare('_advanceRecurring', [date, repeat]);
          compare('_normalizeRepeat', [repeat, new Date(date)]);
          compare('_formatRepeatLabel', [repeat, new Date(date)]);
        }
        for (const name of ['_hasTimeComponent', '_formatDueDate', '_isDueOverdue', '_isDueTodayOrOverdue', '_formatReminderTag']) compare(name, [date]);
        for (const repeat of ['weekly:1', 'monthly:day:31', 'monthly:nth:5:1', 'monthly:last:0', 'daily', 'monthly:bad']) compare('_snapToRepeat', [new Date(date), repeat]);
      }
      for (const name of ['_laterTodayDate', '_tomorrowDate', '_nextWeekDate']) compare(name, []);
      for (const year of [2023, 2024]) for (let month = 0; month < 12; month++) for (let wd = 0; wd < 7; wd++) {
        compare('_nthWeekdayOfMonth', [year, month, wd, 5]);
        compare('_lastWeekdayOfMonth', [year, month, wd]);
      }
    } finally { if (oldZone === undefined) delete process.env.TZ; else process.env.TZ = oldZone; }
  });
}
