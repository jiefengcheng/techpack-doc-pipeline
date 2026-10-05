from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

COLORS = {
    "table": (220, 38, 38),
    "artwork": (37, 99, 235),
    "drawing": (8, 145, 178),
}


def draw_overlay(image: Image.Image, regions) -> Image.Image:
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except OSError:
        font = ImageFont.load_default()

    for region in regions:
        color = COLORS.get(region.kind, (15, 23, 42))
        x0, y0, x1, y1 = region.box
        draw.rectangle([x0, y0, x1, y1], outline=color, width=4)
        tag = region.kind
        draw.rectangle([x0, max(0, y0 - 26), x0 + 10 * len(tag) + 14, y0], fill=color)
        draw.text((x0 + 6, max(0, y0 - 24)), tag, fill=(255, 255, 255), font=font)
    return overlay
