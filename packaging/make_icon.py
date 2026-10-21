"""Draw packaging/searchship.ico: a white magnifying glass on a blue rounded square.

Each size is drawn on its own (at 8x, then scaled down) so the small sizes stay
crisp instead of being shrunk from the 256 px image.

    pip install pillow
    python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (47, 86, 196, 255)   # --accent in tracker/ui/index.html
WHITE = (255, 255, 255, 255)
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parent / "searchship.ico"


def draw(size: int) -> Image.Image:
    k = 8
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 0 if size <= 24 else n * 0.04
    d.rounded_rectangle((pad, pad, n - pad, n - pad), radius=n * 0.22, fill=ACCENT)

    # Thicker strokes at small sizes so the glass still reads at 16 px.
    stroke = n * (0.12 if size <= 24 else 0.095)
    cx, cy, r = n * 0.44, n * 0.43, n * 0.20
    d.ellipse((cx - r - stroke / 2, cy - r - stroke / 2, cx + r + stroke / 2, cy + r + stroke / 2), fill=WHITE)
    d.ellipse((cx - r + stroke / 2, cy - r + stroke / 2, cx + r - stroke / 2, cy + r - stroke / 2), fill=ACCENT)

    # Handle: a round-capped line from the rim toward the lower right.
    x0, y0 = cx + r * 0.72, cy + r * 0.72
    x1, y1 = n * 0.74, n * 0.73
    d.line((x0, y0, x1, y1), fill=WHITE, width=round(stroke * 1.15))
    cap = stroke * 1.15 / 2
    d.ellipse((x1 - cap, y1 - cap, x1 + cap, y1 + cap), fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main():
    images = [draw(s) for s in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"wrote {OUT} ({', '.join(str(s) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
