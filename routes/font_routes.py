"""Custom font discovery — lists user-supplied font files in static/fonts/custom/."""

import os
import re
from fastapi import APIRouter

CUSTOM_FONTS_DIR = os.path.join("static", "fonts", "custom")
FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}


def _derive_family(filename):
    """Derive a font-family name from a filename like 'JetBrainsMono-Regular.woff2' → 'JetBrains Mono'."""
    name = os.path.splitext(filename)[0]
    # Strip common weight/style suffixes
    name = re.sub(
        r"[-_ ]?(Thin|ExtraLight|UltraLight|Light|Regular|Medium|SemiBold|DemiBold|Bold|ExtraBold|UltraBold|Black|Heavy|Italic|Oblique|Variable|VF)$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Insert spaces before uppercase runs: "JetBrainsMono" → "Jet Brains Mono"
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    # Replace dashes/underscores with spaces
    name = re.sub(r"[-_]+", " ", name).strip()
    return name or filename


def setup_font_routes():
    router = APIRouter(prefix="/api/fonts", tags=["fonts"])

    @router.get("/custom")
    async def list_custom_fonts():
        """Return available custom fonts grouped by derived family name."""
        os.makedirs(CUSTOM_FONTS_DIR, exist_ok=True)
        families = {}
        for f in sorted(os.listdir(CUSTOM_FONTS_DIR)):
            ext = os.path.splitext(f)[1].lower()
            if ext not in FONT_EXTENSIONS:
                continue
            family = _derive_family(f)
            if family not in families:
                families[family] = []
            families[family].append(
                {
                    "file": f,
                    "url": f"/static/fonts/custom/{f}",
                    "format": ext.lstrip("."),
                }
            )
        return {"fonts": families}

    return router
