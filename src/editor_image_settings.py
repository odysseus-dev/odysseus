"""The shared model defaults used by Quick Edit's image operations."""
from src.settings import get_user_setting, load_settings


def editor_image_models(owner: str = "") -> dict[str, str]:
    settings = load_settings()
    generation = ""
    edit_model = ""
    if owner:
        generation = str(get_user_setting("image_model", owner=owner, default="") or "").strip()
        edit_model = str(get_user_setting("image_edit_model", owner=owner, default="") or "").strip()
    if not generation:
        generation = str(settings.get("image_model") or "").strip()
    if not edit_model:
        edit_model = str(settings.get("image_edit_model") or generation).strip()
    return {
        "generation": generation,
        "edit": edit_model or generation,
    }
