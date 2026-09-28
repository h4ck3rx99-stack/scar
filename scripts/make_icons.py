"""Draw SCAR's app icon (1024 px master for `tauri icon`) and the tray-state icons with Pillow.

The mark: a rounded teal tile with two parallel diagonal strokes (the "scar"). Tray icons add a status dot:
working (teal), listening (blue), needs approval (amber), error (red); idle has none.

    uv run python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "app" / "src-tauri" / "icons"
TOP, BOTTOM = (0x1B, 0xB8, 0xA4), (0x0A, 0x6B, 0x60)
DOTS = {"working": (0x1B, 0xB8, 0xA4), "listening": (0x3B, 0x82, 0xF6), "approval": (0xF5, 0x9E, 0x0B), "error": (0xE5, 0x48, 0x4D)}


def mark(size: int, dot: tuple[int, int, int] | None = None) -> Image.Image:
    s = size * 4  # supersample, then downscale for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=(*tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)), 255))
    mask = Image.new("L", (s, s), 0)
    inset = s * 4 // 64
    ImageDraw.Draw(mask).rounded_rectangle([inset, inset, s - inset, s - inset], radius=s * 16 // 64, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    u = s / 64
    d.line([(19 * u, 41 * u), (43 * u, 17 * u)], fill=(255, 255, 255, 255), width=int(7 * u))
    for x, y in ((19, 41), (43, 17)):
        d.ellipse([(x - 3.5) * u, (y - 3.5) * u, (x + 3.5) * u, (y + 3.5) * u], fill=(255, 255, 255, 255))
    d.line([(29 * u, 47 * u), (46 * u, 30 * u)], fill=(255, 255, 255, 153), width=int(5 * u))
    for x, y in ((29, 47), (46, 30)):
        d.ellipse([(x - 2.5) * u, (y - 2.5) * u, (x + 2.5) * u, (y + 2.5) * u], fill=(255, 255, 255, 153))
    if dot is not None:
        r = s * 0.2
        cx, cy = s - r - s * 0.02, s - r - s * 0.02
        d.ellipse([cx - r - u * 3, cy - r - u * 3, cx + r + u * 3, cy + r + u * 3], fill=(255, 255, 255, 255))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*dot, 255))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    mark(1024).save(ICONS / "app-icon-1024.png")
    for state, color in [("idle", None), *DOTS.items()]:
        mark(32, color).save(ICONS / f"tray-{state}.png")
        mark(64, color).save(ICONS / f"tray-{state}@2x.png")
    print(f"wrote icons to {ICONS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
