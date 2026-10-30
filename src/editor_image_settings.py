"""The shared model defaults used by Quick Edit's image operations."""
from src.settings import load_settings


def editor_image_models():
    settings = load_settings()
    generation = str(settings.get("image_model") or "").strip()
    return {
        "generation": generation,
        "edit": str(settings.get("image_edit_model") or generation).strip(),
    }
