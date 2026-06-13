import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "static" / "manifest.json"


def test_manifest_icons_exist_on_disk():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for icon in data["icons"]:
        disk = ROOT / icon["src"].removeprefix("/")
        assert disk.is_file(), f"missing icon: {disk}"
