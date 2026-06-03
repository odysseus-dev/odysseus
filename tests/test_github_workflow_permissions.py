from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _uses_checkout(value):
    if isinstance(value, dict):
        return any(_uses_checkout(v) for v in value.values())
    if isinstance(value, list):
        return any(_uses_checkout(v) for v in value)
    return isinstance(value, str) and value.startswith("actions/checkout@")


def test_workflows_using_checkout_keep_contents_read_permission():
    """Explicit workflow permissions reset unspecified scopes to none.

    `actions/checkout` needs repository content access, so workflows that
    declare a custom permission block and still checkout the repo must keep
    `contents: read` in that block.
    """
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "permissions" not in data or not _uses_checkout(data):
            continue
        contents_permission = (data.get("permissions") or {}).get("contents")
        if contents_permission != "read":
            offenders.append(path.name)

    assert offenders == []
