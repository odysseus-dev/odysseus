from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "services" / "memory" / "skill_format.py"


def test_skill_format_timestamp_uses_aware_utc():
    source = SOURCE.read_text(encoding="utf-8")

    assert "datetime.now(timezone.utc)" in source


def test_skill_format_timestamp_avoids_utcnow():
    source = SOURCE.read_text(encoding="utf-8")

    assert "datetime.utcnow()" not in source
