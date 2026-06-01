"""Generate PWA icons for Odysseus — boat logo at 192x192 and 512x512."""
from PIL import Image, ImageDraw
import os


def create_icon(size, filename):
    accent = "#e06c75"
    bg = "#1a1a2e"

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    r = size // 6
    draw.rounded_rectangle([0, 0, size, size], radius=r, fill=bg)

    scale = size / 32.0

    hull_y = 22 * scale
    draw.arc(
        [4 * scale, hull_y - 4 * scale, 28 * scale, hull_y + 4 * scale],
        start=195, end=345,
        fill=accent,
        width=max(2, int(2.5 * scale)),
    )

    sail_mid = 16 * scale
    sail_bottom = 22 * scale
    draw.polygon([
        (sail_mid, 3 * scale),
        (5 * scale, sail_bottom),
        (sail_mid, sail_bottom),
    ], fill=accent)
    draw.polygon([
        (sail_mid, 7 * scale),
        (24 * scale, sail_bottom),
        (sail_mid, sail_bottom),
    ], fill="#d4717a")

    img.save(filename, "PNG")
    print(f"Created {filename} ({size}x{size})")


def create_maskable_icon(size, filename):
    accent = "#e06c75"
    bg = "#1a1a2e"
    safe_margin = size * 0.1875

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [safe_margin, safe_margin, size - safe_margin, size - safe_margin],
        radius=size // 8,
        fill=bg,
    )

    usable = size - 2 * safe_margin
    scale = usable / 32.0
    ox = safe_margin
    oy = safe_margin

    hull_y = oy + 22 * scale
    draw.arc(
        [ox + 4 * scale, hull_y - 4 * scale, ox + 28 * scale, hull_y + 4 * scale],
        start=195, end=345,
        fill=accent,
        width=max(2, int(2.5 * scale)),
    )

    draw.polygon([
        (ox + 16 * scale, oy + 3 * scale),
        (ox + 5 * scale, oy + 22 * scale),
        (ox + 16 * scale, oy + 22 * scale),
    ], fill=accent)
    draw.polygon([
        (ox + 16 * scale, oy + 7 * scale),
        (ox + 24 * scale, oy + 22 * scale),
        (ox + 16 * scale, oy + 22 * scale),
    ], fill="#d4717a")

    img.save(filename, "PNG")
    print(f"Created {filename} ({size}x{size} maskable)")


if __name__ == "__main__":
    static_dir = os.path.dirname(os.path.abspath(__file__))
    create_icon(192, os.path.join(static_dir, "icon-192.png"))
    create_icon(512, os.path.join(static_dir, "icon-512.png"))
    create_maskable_icon(192, os.path.join(static_dir, "icon-192-maskable.png"))
    create_maskable_icon(512, os.path.join(static_dir, "icon-512-maskable.png"))
    create_icon(180, os.path.join(static_dir, "apple-touch-icon.png"))
    print("Done - all icons generated.")
