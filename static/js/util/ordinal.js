// Pure (browser-free) English ordinal suffix, e.g. 1 -> "st", 21 -> "st",
// 22 -> "nd", 23 -> "rd", 11/12/13 -> "th". Extracted so it can be unit-tested.
export function ordinalSuffix(n) {
  const a = Math.abs(Math.trunc(Number(n) || 0));
  const mod100 = a % 100;
  if (mod100 >= 11 && mod100 <= 13) return 'th';
  switch (a % 10) {
    case 1: return 'st';
    case 2: return 'nd';
    case 3: return 'rd';
    default: return 'th';
  }
}
