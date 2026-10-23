"""Generates the bundled local images (default avatars, demo pet DPs, demo post photos) with Pillow.
No external image service is required."""
import os
import random

from PIL import Image, ImageDraw

PALETTES = [  # (background, fur, ear, muzzle)
    ("#cfe3ff", "#e9b872", "#c58a3e", "#fff1d6"), ("#ffd9e8", "#f4f1ea", "#d7cfc2", "#ffffff"),
    ("#fff1b8", "#8a5a3b", "#5e3b24", "#e9c9a5"), ("#d9f7ee", "#4a4a55", "#2e2e38", "#d9d9e0"),
    ("#e6dcff", "#d9a066", "#a86f35", "#f7e2c4"), ("#ffe0c2", "#f7f0e1", "#e5a15a", "#ffffff"),
    ("#cdeffd", "#b8b8c4", "#8c8c9a", "#f0f0f5"), ("#ffd6d6", "#2f2f33", "#1b1b1e", "#8a6b4f"),
]


def make_avatar(path, idx=0, size=400):
    bg, fur, ear, muz = PALETTES[idx % len(PALETTES)]
    s = size
    im = Image.new("RGB", (s, s), bg)
    d = ImageDraw.Draw(im)
    style = idx % 3
    if style == 0:      # floppy ears
        d.ellipse([s*.08, s*.22, s*.34, s*.74], fill=ear); d.ellipse([s*.66, s*.22, s*.92, s*.74], fill=ear)
    elif style == 1:    # pointy ears
        d.polygon([(s*.18, s*.10), (s*.40, s*.30), (s*.14, s*.50)], fill=ear)
        d.polygon([(s*.82, s*.10), (s*.60, s*.30), (s*.86, s*.50)], fill=ear)
    else:               # round ears
        d.ellipse([s*.12, s*.12, s*.38, s*.38], fill=ear); d.ellipse([s*.62, s*.12, s*.88, s*.38], fill=ear)
    d.ellipse([s*.18, s*.2, s*.82, s*.84], fill=fur)
    d.ellipse([s*.32, s*.5, s*.68, s*.80], fill=muz)
    for cx in (.36, .64):
        d.ellipse([s*(cx-.05), s*.4, s*(cx+.05), s*.52], fill="#1b1b2f")
        d.ellipse([s*(cx-.02), s*.42, s*(cx+.01), s*.45], fill="white")
    d.ellipse([s*.44, s*.54, s*.56, s*.62], fill="#1b1b2f")
    d.arc([s*.38, s*.58, s*.50, s*.70], 10, 170, fill="#1b1b2f", width=4)
    d.arc([s*.50, s*.58, s*.62, s*.70], 10, 170, fill="#1b1b2f", width=4)
    d.ellipse([s*.46, s*.66, s*.54, s*.76], fill="#ff7aa8")
    d.ellipse([s*.24, s*.56, s*.33, s*.63], fill="#ffb3cf"); d.ellipse([s*.67, s*.56, s*.76, s*.63], fill="#ffb3cf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)


def _paw(d, x, y, r, fill):
    d.ellipse([x-r, y-r*.6, x+r, y+r*1.1], fill=fill)
    for dx, dy in ((-1.1, -1.0), (-.4, -1.6), (.4, -1.6), (1.1, -1.0)):
        d.ellipse([x+dx*r-r*.38, y+dy*r-r*.45, x+dx*r+r*.38, y+dy*r+r*.45], fill=fill)


def make_post_image(path, seed=0, avatar_idx=0, w=800, h=600):
    rnd = random.Random(seed)
    skies = [("#8ec5ff", "#fff1b8"), ("#ffb3cf", "#ffe9a8"), ("#a8e6cf", "#e6f7ff"), ("#c3b1ff", "#ffd9e8"), ("#ffd29c", "#fff7d6")]
    top, bot = skies[seed % len(skies)]
    t = tuple(int(top[i:i+2], 16) for i in (1, 3, 5)); b = tuple(int(bot[i:i+2], 16) for i in (1, 3, 5))
    im = Image.new("RGB", (w, h)); d = ImageDraw.Draw(im)
    for y in range(h):
        f = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i]-t[i]) * f) for i in range(3)))
    d.ellipse([w*.7, h*.08, w*.7+110, h*.08+110], fill="#ffe27a")
    for _ in range(3):
        cx, cy = rnd.randint(40, w-200), rnd.randint(30, 160)
        for k in range(3):
            d.ellipse([cx+k*40, cy-k % 2*14, cx+k*40+90, cy+40], fill="white")
    d.ellipse([-150, h*.68, w*.65, h*1.4], fill="#7bd88f"); d.ellipse([w*.3, h*.74, w*1.3, h*1.5], fill="#5cc47a")
    av = os.path.join(os.path.dirname(path), "_tmp_av.png")
    make_avatar(av, avatar_idx, 280)
    a = Image.open(av).convert("RGB"); os.remove(av)
    mask = Image.new("L", a.size, 0); ImageDraw.Draw(mask).ellipse([0, 0, a.size[0], a.size[1]], fill=255)
    px, py = int(w*.5 - 140 + rnd.randint(-120, 120)), int(h*.38 + rnd.randint(-20, 30))
    d.ellipse([px-8, py-8, px+288, py+288], fill="white")
    im.paste(a, (px, py), mask)
    for _ in range(5):
        _paw(d, rnd.randint(40, w-40), rnd.randint(int(h*.8), h-30), rnd.randint(9, 16), "#ffffff")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, quality=84)


def ensure_default_avatars(static_dir):
    for i in range(len(PALETTES)):
        p = os.path.join(static_dir, "images", f"avatar_{i}.png")
        if not os.path.exists(p):
            make_avatar(p, i)
    p = os.path.join(static_dir, "images", "paw.svg")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><g fill="#3b82f6"><ellipse cx="32" cy="42" rx="14" ry="11"/><ellipse cx="14" cy="28" rx="6" ry="8"/><ellipse cx="26" cy="16" rx="6" ry="8"/><ellipse cx="38" cy="16" rx="6" ry="8"/><ellipse cx="50" cy="28" rx="6" ry="8"/></g></svg>')
