//! Unit checks for `pydatetime::to_isoformat` against Python `datetime.isoformat`
//! ground truth (see the values produced by CPython in the dev notes):
//!   "2024-01-01 12:00:00.000000" -> "2024-01-01T12:00:00"   (microsecond==0 omitted)
//!   "2024-01-01 12:00:00.500000" -> "2024-01-01T12:00:00.500000"
//!   "2024-01-01 12:00:00"        -> "2024-01-01T12:00:00"
//!   "2026-05-31 23:30:00.123456" -> "2026-05-31T23:30:00.123456"

use odysseus::pydatetime::to_isoformat;

#[test]
fn to_isoformat_matches_python() {
    assert_eq!(to_isoformat("2024-01-01 12:00:00.000000"), "2024-01-01T12:00:00");
    assert_eq!(to_isoformat("2024-01-01 12:00:00.500000"), "2024-01-01T12:00:00.500000");
    assert_eq!(to_isoformat("2024-01-01 12:00:00"), "2024-01-01T12:00:00");
    assert_eq!(to_isoformat("2026-05-31 23:30:00.123456"), "2026-05-31T23:30:00.123456");
    // Unparseable input passes through unchanged (best effort).
    assert_eq!(to_isoformat("not-a-date"), "not-a-date");
}
