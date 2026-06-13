import os
from PIL import Image, ImageDraw

def generate_icon(size, filename):
    S = size
    factor = 4
    super_S = S * factor
    sc = super_S / 32.0
    img = Image.new("RGBA", (super_S, super_S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    accent = (224, 108, 117, 255)   # #e06c75 (brand salmon)
    accent2 = (180, 86, 94, 255)    # darker shade for the second sail

    def P(x, y):
        return (x * sc, y * sc)

    # sails
    d.polygon([P(16, 4), P(16, 22), P(6, 22)], fill=accent)
    d.polygon([P(16, 8), P(16, 22), P(24, 22)], fill=accent2)

    # wave (two quadratic bezier curves)
    def quad(p0, p1, p2, n=160):
        out = []
        for i in range(n + 1):
            t = i / n
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            out.append((x, y))
        return out

    wave = quad(P(4, 24.5), P(10, 20.5), P(16, 24.5)) + quad(P(16, 24.5), P(22, 28.5), P(28, 24.5))
    d.line(wave, fill=accent, width=int(2.2 * sc), joint="curve")

    # Downscale with high quality filtering (LANCZOS)
    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        try:
            resample_filter = Image.LANCZOS
        except AttributeError:
            resample_filter = Image.ANTIALIAS

    img_resized = img.resize((S, S), resample_filter)
    out_path = os.path.join("/home/ubuntu2/Odysseus/odysseus/static", filename)
    img_resized.save(out_path)
    print(f"Generated anti-aliased transparent icon: {out_path} ({size}x{size})")

if __name__ == "__main__":
    generate_icon(192, "icon-192.png")
    generate_icon(512, "icon-512.png")

