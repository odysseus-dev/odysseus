"""Generate the Odysseus app-icon set from its brand mark (the sailboat).

Reproduces the app's favicon SVG (red sails + wave, accent #e06c75) on a dark
rounded tile, then emits the PNG sizes Tauri references plus a multi-resolution
Windows .ico (what gets embedded in the exe + shown on the taskbar).
"""

import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src-tauri", "icons")
RED = (224, 108, 117)   # #e06c75 — Odysseus accent
BG = (20, 22, 28)       # #14161c — dark tile (matches the app theme)
MASTER = 1024


def quad(p0, p1, p2, t):
    """Point on a quadratic Bezier at parameter t."""
    mt = 1 - t
    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
    return (x, y)


def build_master():
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))

    # Dark rounded-square tile.
    tile = Image.new("RGBA", (MASTER, MASTER), BG + (255,))
    mask = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, MASTER - 1, MASTER - 1], radius=int(MASTER * 0.18), fill=255
    )
    img.paste(tile, (0, 0), mask)

    # Map the 32x32 SVG viewBox into a centered inner box.
    inner = int(MASTER * 0.62)
    off = (MASTER - inner) // 2
    scale = inner / 32.0

    def T(x, y):
        return (off + x * scale, off + y * scale)

    # Left sail (solid).
    ImageDraw.Draw(img, "RGBA").polygon(
        [T(16, 4), T(16, 22), T(6, 22)], fill=RED + (255,)
    )

    # Right sail (60% opacity) — composite via its own layer.
    layer = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    ImageDraw.Draw(layer, "RGBA").polygon(
        [T(16, 8), T(16, 22), T(24, 22)], fill=RED + (153,)
    )
    img = Image.alpha_composite(img, layer)

    # Wave: two quadratic Beziers, stroked.
    draw = ImageDraw.Draw(img, "RGBA")
    pts = [T(*quad((4, 24), (10, 20), (16, 24), i / 60)) for i in range(61)]
    pts += [T(*quad((16, 24), (22, 28), (28, 24), i / 60)) for i in range(61)]
    w = max(2, int(2.5 * scale))
    draw.line(pts, fill=RED + (255,), width=w, joint="curve")
    r = w / 2
    for end in (pts[0], pts[-1]):
        draw.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=RED + (255,))

    return img


def main():
    master = build_master()
    master.save(os.path.join(OUT, "icon.png"))
    for size, name in [(32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")]:
        master.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, name))
    master.save(
        os.path.join(OUT, "icon.ico"),
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("Icons written to", OUT)


if __name__ == "__main__":
    main()
