"""Guard: cookbook_state.json must be located via DATA_DIR, not hardcoded /app/data
(which breaks native runs) or a relative os.environ fallback."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = [
    "src/cookbook_serve_lifecycle.py",
    "src/builtin_actions.py",
    "routes/codex_routes.py",
    "routes/cookbook_routes.py",
]


def test_no_hardcoded_app_data_cookbook_state():
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for ln in text.splitlines():
            if ln.strip().startswith("#"):
                continue
            assert "/app/data/cookbook_state" not in ln, f"{rel}: hardcoded /app/data: {ln.strip()}"
            assert 'os.environ.get("DATA_DIR"' not in ln, f"{rel}: relative DATA_DIR env fallback: {ln.strip()}"


def test_cookbook_state_uses_datadir_constant():
    # Each file that references cookbook_state.json should import the DATA_DIR constant.
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "cookbook_state.json" in text:
            assert "from core.constants import DATA_DIR" in text, f"{rel}: missing DATA_DIR import"
