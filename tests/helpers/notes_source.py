"""Read deployed Notes responsibilities for source guards; runtime tests execute their real modules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def notes_source(*modules):
    base = ROOT / "static/js/features/notes"
    paths = [base / f"{module}.js" for module in modules] if modules else sorted(base.rglob("*.js"))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)
