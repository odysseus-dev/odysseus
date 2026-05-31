"""Cross-platform clean script: removes .venv, __pycache__, .pytest_cache, and *.pyc files."""
import shutil
import pathlib

root = pathlib.Path(__file__).parent.parent

targets = [".venv"]
for pattern in ("__pycache__", ".pytest_cache"):
    targets.extend(str(p) for p in root.rglob(pattern) if p.is_dir())

for t in targets:
    p = root / t if not pathlib.Path(t).is_absolute() else pathlib.Path(t)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
        print(f"removed {p}")

for pyc in root.rglob("*.pyc"):
    pyc.unlink(missing_ok=True)

print("clean done")
