"""Server-side completion rules shared by Notes reminder entry points."""
import json


def note_is_fully_done(note_type, items) -> bool:
    """Match Notes' nonempty, fully checked todo/goal/checklist rule."""
    if note_type not in ("todo", "goal", "checklist"):
        return False
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            return False
    return isinstance(items, list) and bool(items) and all(
        isinstance(item, dict) and bool(item.get("done")) for item in items
    )
