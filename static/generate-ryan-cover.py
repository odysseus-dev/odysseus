#!/usr/bin/env python3
"""
Ryan Celsius° Sounds — Cover Art Generator
Vaporwave/lo-fi aesthetic: purple/pink neon, retro cityscape, VHS grain, sunset grid
"""
import random, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1000, 1000
img = Image.new('RGB', (W, H))
draw = ImageDraw.Draw(img)

# ── Sky gradient ──
sky_colors = [
    (0.00, (10, 0, 21)),
    (0.20, (26, 0, 48)),
    (0.40, (61, 0, 102)),
    (0.55, (107, 0, 153)),
    (0.65, (204, 0, 102)),
    (0.75, (255, 51, 102)),
    (0.85, (255, 102, 51)),
    (0.92, (255, 170, 0)),
    (1.00, (255, 221, 68)),
]

def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

for y in range(H):
    frac = y / H
    for i in range(len(sky_colors) - 1):
        if sky_colors[i][0] <= frac <= sky_colors[i+1][0]:
            t = (frac - sky_colors[i][0]) / (sky_colors[i+1][0] - sky_colors[i][0])
            color = lerp_color(sky_colors[i][1], sky_colors[i+1][1], t)
            draw.line([(0, y), (W, y)], fill=color)
            break

# ── Stars ──
for _ in range(60):
    sx, sy = random.randint(0, W), random.randint(0, 620)
    sr = random.randint(1, 3)
    a = random.randint(80, 200)
    draw.ellipse([sx-sr, sy-sr, sx+sr, sy+sr], fill=(255, 255, 255, a))

# ── Sun ──
for r in range(160, 0, -1):
    t = r / 160
    if t > 0.6:
        c = lerp_color((204, 0, 102), (255, 102, 51), (t - 0.6) / 0.4)
    elif t > 0.3:
        c = lerp_color((255, 102, 51), (255, 221, 102), (t - 0.3) / 0.3)
    else:
        c = lerp_color((255, 221, 102), (255, 255, 255), t / 0.3)
    draw.ellipse([500-r, 580-r, 500+r, 580+r], fill=c)

# Sun stripes
stripe_data = [(540, 6, 320), (558, 8, 300), (580, 10, 280), (605, 12, 250), (632, 14, 210)]
for sy, sh, sw in stripe_data:
    draw.rectangle([500-sw//2, sy, 500+sw//2, sy+sh], fill=(10, 0, 21))

# ── Vaporwave grid ──
horiz = [(0, 1.5, 150), (30, 0.8, 120), (65, 0.6, 100), (105, 0.5, 75), (150, 0.4, 50), (200, 0.3, 38), (260, 0.3, 25), (330, 0.2, 12)]
for hy, lw, alpha in horiz:
    c = (255, 0, 255, alpha)
    draw.line([(0, 640+hy), (W, 640+hy)], fill=c, width=max(1, int(lw)))

for vx in [-200, 0, 200, 350, 500, 650, 800, 1000, 1200]:
    dist = abs(vx - 500)
    alpha = max(25, 100 - dist // 10)
    draw.line([(500, 640), (vx, 1000)], fill=(255, 0, 255, alpha), width=1)

# ── Buildings ──
buildings = [
    (0, 460, 60, 180, '#0a0020'),
    (70, 420, 80, 220, '#0d0025'),
    (160, 480, 50, 160, '#080020'),
    (220, 390, 100, 250, '#0f0030'),
    (340, 350, 70, 290, '#100035'),
    (580, 400, 90, 240, '#0f0030'),
    (680, 440, 65, 200, '#0a0025'),
    (755, 380, 80, 260, '#100035'),
    (845, 460, 55, 180, '#0a0020'),
    (910, 430, 90, 210, '#0d0025'),
]

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

neon_colors = [(255, 0, 255), (0, 255, 255), (255, 102, 51)]

for bx, by, bw, bh, bc in buildings:
    base = hex_to_rgb(bc)
    draw.rectangle([bx, by, bx+bw, by+bh], fill=base, outline=(107, 0, 153))

    # Windows
    win_w, win_h = min(14, bw//4), 10
    cols = max(1, (bw - 16) // (win_w + 4))
    rows = max(1, (bh - 20) // (win_h + 8))
    for r in range(rows):
        for cc in range(cols):
            if random.random() > 0.4:
                nc = random.choice(neon_colors)
                a = random.randint(30, 70)
                wx = bx + 8 + cc * (win_w + 4)
                wy = by + 10 + r * (win_h + 8)
                overlay = Image.new('RGBA', (win_w, win_h), nc + (a,))
                img.paste(Image.alpha_composite(
                    img.crop((wx, wy, wx+win_w, wy+win_h)).convert('RGBA'),
                    overlay
                ).convert('RGB'), (wx, wy))

# Antenna
draw.rectangle([268, 370, 272, 390], fill=hex_to_rgb('#0f0030'))
draw.ellipse([267, 365, 273, 371], fill=(255, 51, 102))

# Neon OPEN sign
try:
    font_mono = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 72)
    font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 36)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
    font_vhs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
except:
    font_mono = ImageFont.load_default()
    font_title = font_sub = font_small = font_vhs = font_mono

# Glow function
def draw_glow_text(draw, pos, text, font, color, glow_radius=15):
    gx, gy = pos
    glow_img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_img)
    gd.text((gx, gy), text, font=font, fill=color + (120,), anchor='mm' if isinstance(gx, int) and isinstance(gy, int) else None)
    for _ in range(3):
        glow_img = glow_img.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(Image.alpha_composite(img.convert('RGBA'), glow_img).convert('RGB'), (0, 0))
    draw.text(pos, text, font=font, fill=color, anchor=None)

# OPEN sign with glow
glow_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow_layer)
bbox = font_mono.getbbox('OPEN')
tw = bbox[2] - bbox[0]
gd.text((270 - tw//2, 500), 'OPEN', font=font_mono, fill=(0, 255, 255, 200))
for _ in range(4):
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(12))
img.paste(Image.alpha_composite(img.convert('RGBA'), glow_layer).convert('RGB'), (0, 0))
draw.text((270 - tw//2, 500), 'OPEN', font=font_mono, fill=(0, 255, 255))

# ── Title: RYAN CELSIUS° with chromatic aberration ──
title = 'RYAN CELSIUS°'
bbox = font_title.getbbox(title)
tw = bbox[2] - bbox[0]
tx = 500 - tw // 2
ty = 180

# Chromatic aberration — red offset
draw.text((tx + 2, ty), title, font=font_title, fill=(255, 0, 0, 150))
# Blue offset
draw.text((tx - 2, ty), title, font=font_title, fill=(0, 102, 255, 150))

# Glow layer for title
title_glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
tg_draw = ImageDraw.Draw(title_glow)
tg_draw.text((tx, ty), title, font=font_title, fill=(255, 255, 255, 200))
for _ in range(5):
    title_glow = title_glow.filter(ImageFilter.GaussianBlur(20))
img.paste(Image.alpha_composite(img.convert('RGBA'), title_glow).convert('RGB'), (0, 0))

# White title
draw.text((tx, ty), title, font=font_title, fill=(255, 255, 255))

# SOUNDS subtitle with glow
sub = 'S O U N D S'
bbox2 = font_sub.getbbox(sub)
sw = bbox2[2] - bbox2[0]
sx = 500 - sw // 2
sy = 260

sub_glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
sg = ImageDraw.Draw(sub_glow)
sg.text((sx, sy), sub, font=font_sub, fill=(0, 255, 255, 180))
for _ in range(4):
    sub_glow = sub_glow.filter(ImageFilter.GaussianBlur(15))
img.paste(Image.alpha_composite(img.convert('RGBA'), sub_glow).convert('RGB'), (0, 0))
draw.text((sx, sy), sub, font=font_sub, fill=(0, 255, 255))

# Decorative line
line_glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
lg = ImageDraw.Draw(line_glow)
lg.line([(250, 300), (750, 300)], fill=(255, 0, 255, 120), width=1)
for _ in range(3):
    line_glow = line_glow.filter(ImageFilter.GaussianBlur(6))
img.paste(Image.alpha_composite(img.convert('RGBA'), line_glow).convert('RGB'), (0, 0))
draw.line([(250, 300), (750, 300)], fill=(255, 0, 255, 128))

# Tagline
tagline = 'LATE NIGHT DRIVES // 2 AM VIBES'
bbox3 = font_small.getbbox(tagline)
tw3 = bbox3[2] - bbox3[0]
draw.text((500 - tw3//2, 320), tagline, font=font_small, fill=(255, 102, 51, 178))

# ── VHS tracking lines ──
for (ly, lh, lc) in [(150, 3, (255,255,255)), (380, 2, (0,255,255)), (520, 4, (255,0,255)), (700, 2, (255,255,255)), (850, 3, (255,102,51))]:
    draw.rectangle([0, ly, W, ly+lh], fill=lc + (30,))

# ── Scanlines ──
scanline = Image.new('RGBA', (W, H), (0, 0, 0, 0))
sd = ImageDraw.Draw(scanline)
for sl in range(0, H, 4):
    sd.rectangle([0, sl, W, sl+2], fill=(0, 0, 0, 15))
img = Image.alpha_composite(img.convert('RGBA'), scanline).convert('RGB')
draw = ImageDraw.Draw(img)

# ── VHS timestamp ──
draw.text((40, 950), '● REC', font=font_vhs, fill=(255, 255, 255, 100))
ts = '02:47 AM'
bbox4 = font_vhs.getbbox(ts)
draw.text((960 - (bbox4[2]-bbox4[0]), 950), ts, font=font_vhs, fill=(255, 255, 255, 128))

# ── Corner brackets ──
bracket_color = (255, 0, 255, 76)
draw.line([(20, 50), (20, 20), (50, 20)], fill=bracket_color, width=1)
draw.line([(950, 20), (980, 20), (980, 50)], fill=bracket_color, width=1)
draw.line([(20, 950), (20, 980), (50, 980)], fill=bracket_color, width=1)
draw.line([(950, 980), (980, 980), (980, 950)], fill=bracket_color, width=1)

# Thin neon border
draw.rectangle([10, 10, 980, 980], outline=(255, 0, 255, 50))

# ── Save ──
out = '/home/donn/odysseus/static/ryan-celsius-cover.png'
img.save(out, 'PNG', quality=95)
print(f'Saved: {out} ({W}x{H})')
