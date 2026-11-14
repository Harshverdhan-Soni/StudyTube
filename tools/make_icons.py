"""Generates StudyTube's icon set and splash animation.

Outputs (relative to the app folder):
  icon.ico            multi-size Windows icon (16..256), small sizes hand-simplified
  icon.png            512 px (Linux / macOS)
  static/icon-192.png favicon / window icon for the page
  static/splash.gif   animated logo for the start-up screen
Run:  python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
NAVY_TOP, NAVY_BOT = (40, 78, 122), (18, 38, 66)
ORANGE = (247, 147, 30)
PAGE_L, PAGE_R = (255, 255, 255), (232, 238, 246)
LINE = (150, 168, 192)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def background(S, radius_frac=0.225):
    """Rounded-square tile with a soft vertical gradient."""
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), lerp(NAVY_TOP, NAVY_BOT, y / (S - 1)))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * radius_frac), fill=255)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    return tile


def book(d, S, simple):
    """Open book: lines on the left page (read), play mark on the right page (watch).
    `flip` in 0..1 draws a page turning from right to left (splash animation)."""
    u = S / 100
    top, bot, sag = 30 * u, 78 * u, 5 * u   # page top/bottom and the dip at the spine
    lx, rx, cx = 14 * u, 86 * u, 50 * u
    gap = (2.2 if not simple else 3.0) * u
    if simple:  # bigger, bolder for 16-32 px
        top, bot, lx, rx = 26 * u, 80 * u, 10 * u, 90 * u

    if not simple:  # thin cover edge under the pages
        d.polygon([(lx, bot), (cx, bot + sag), (rx, bot), (rx, bot + 4 * u),
                   (cx, bot + 4 * u + sag), (lx, bot + 4 * u)], fill=(11, 24, 42))
    left = [(lx, top), (cx - gap, top + sag), (cx - gap, bot + sag), (lx, bot)]
    right = [(cx + gap, top + sag), (rx, top), (rx, bot), (cx + gap, bot + sag)]
    d.polygon(left, fill=PAGE_L)
    d.polygon(right, fill=PAGE_R)

    if not simple:  # text lines on the left page
        for i in range(4):
            y = top + (12 + i * 10) * u
            w = 0.62 if i == 3 else 0.8
            x0, x1 = lx + 6 * u, lx + 6 * u + (cx - gap - lx - 12 * u) * w
            frac0, frac1 = (x0 - lx) / (cx - lx), (x1 - lx) / (cx - lx)
            d.line([(x0, y + sag * frac0), (x1, y + sag * frac1)], fill=LINE, width=max(1, int(3.2 * u)))

    # play triangle on the right page
    pcx, pcy = (cx + rx) / 2 + (1.5 * u), (top + bot) / 2 + sag / 2
    r = (15 if simple else 12) * u
    tri = [(pcx - r * 0.75, pcy - r), (pcx - r * 0.75, pcy + r), (pcx + r, pcy)]
    d.polygon(tri, fill=ORANGE)

    return (lx, rx, cx, top, bot, sag, u)


def turning_page(img, geom, flip):
    """Semi-transparent page sweeping right -> left, so the logo stays readable under it."""
    lx, rx, cx, top, bot, sag, u = geom
    x = rx + (lx - rx) * flip
    lift = 7 * u * (1 - abs(0.5 - flip) * 2)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon([(cx, top + sag), (x, top - lift), (x, bot - lift), (cx, bot + sag)],
                                  fill=(255, 255, 255, 150))
    return Image.alpha_composite(img, layer)


def render(px, simple=None, flip=None, scale=4):
    """Draw at `scale`x and downsample for clean anti-aliasing."""
    simple = px <= 32 if simple is None else simple
    S = px * scale
    img = background(S)
    d = ImageDraw.Draw(img, "RGBA")
    geom = book(d, S, simple)
    if flip is not None:
        img = turning_page(img, geom, flip)
    return img.resize((px, px), Image.LANCZOS)


def main():
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]
    if 16 in sizes:  # tiny sizes: a touch of sharpening
        frames[0] = frames[0].filter(ImageFilter.UnsharpMask(radius=0.6, percent=60))
    frames[-1].save(ROOT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    render(512).save(ROOT / "icon.png")
    (ROOT / "static").mkdir(exist_ok=True)
    render(192).save(ROOT / "static" / "icon-192.png")

    # splash animation: page turns, then a short rest
    bg = (22, 22, 20)
    out = []
    steps = [None] * 4 + [0.06 + 0.88 * i / 13 for i in range(14)] + [None] * 4
    for f in steps:
        tile = render(200, simple=False, flip=f, scale=3)
        frame = Image.new("RGB", (240, 240), bg)
        frame.paste(tile, (20, 20), tile)
        out.append(frame.convert("P", palette=Image.ADAPTIVE, colors=128))
    out[0].save(ROOT / "static" / "splash.gif", save_all=True, append_images=out[1:],
                duration=[90] * 4 + [55] * 14 + [90] * 4, loop=0, disposal=2)
    print("icons written")


if __name__ == "__main__":
    main()
