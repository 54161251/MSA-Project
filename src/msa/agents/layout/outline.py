"""Draw a plain room outline (walls, doors, 1 m grid, dimensions) as a PNG.

Used to create the demo floor plan image. Admins normally upload their own drawing (from
Lucidchart, SmartDraw or a scan); any image works as long as the room's width and height in
metres are entered with it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PX_PER_M = 100
MARGIN = 60


def draw_outline(width_m: float, height_m: float, exits: list[dict], path: Path) -> Path:
    w, h = int(width_m * PX_PER_M), int(height_m * PX_PER_M)
    img = Image.new("RGB", (w + 2 * MARGIN, h + 2 * MARGIN), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    ox, oy = MARGIN, MARGIN
    for i in range(1, int(width_m)):
        x = ox + i * PX_PER_M
        d.line([(x, oy), (x, oy + h)], fill=(232, 232, 232), width=1)
    for j in range(1, int(height_m)):
        y = oy + j * PX_PER_M
        d.line([(ox, y), (ox + w, y)], fill=(232, 232, 232), width=1)
    d.rectangle([ox, oy, ox + w, oy + h], outline=(40, 40, 40), width=8)
    for ex in exits:
        x, y = ox + ex["x"] * PX_PER_M, oy + ex["y"] * PX_PER_M
        half = 0.5 * PX_PER_M
        if ex["y"] in (0, height_m):
            d.line([(x - half, y), (x + half, y)], fill="white", width=10)
            d.arc([x - half, y - 2 * half, x + half, y], 0, 180, fill=(120, 120, 120), width=2)
        else:
            d.line([(x, y - half), (x, y + half)], fill="white", width=10)
        d.text((x + 8, y - 26 if ex["y"] else y + 8), ex.get("label", "Exit"), fill=(0, 110, 0),
               font=font)
    ft = 3.28084
    d.text((ox + w / 2 - 60, oy + h + 18), f"{width_m:g} m ({width_m * ft:.1f} ft)", fill="black",
           font=font)
    d.text((8, oy + h / 2), f"{height_m:g} m", fill="black", font=font)
    d.text((8, oy + h / 2 + 22), f"({height_m * ft:.1f} ft)", fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def image_box(width_m: float, height_m: float) -> dict:
    """Where the room sits inside an image drawn by draw_outline (for the UI overlay)."""
    return {"margin_px": MARGIN, "px_per_m": PX_PER_M,
            "width_px": int(width_m * PX_PER_M) + 2 * MARGIN,
            "height_px": int(height_m * PX_PER_M) + 2 * MARGIN}
