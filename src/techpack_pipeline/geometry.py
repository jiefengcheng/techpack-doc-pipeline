from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PixelBox:
    x0: int
    y0: int
    x1: int
    y1: int

    def clamp(self, width: int, height: int, pad: int = 0) -> "PixelBox":
        return PixelBox(
            x0=max(0, self.x0 - pad),
            y0=max(0, self.y0 - pad),
            x1=min(width, self.x1 + pad),
            y1=min(height, self.y1 + pad),
        )

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)


def page_box_to_pixels(
    box: dict,
    page_w: float,
    page_h: float,
    img_w: int,
    img_h: int,
) -> PixelBox:
    sx, sy = img_w / page_w, img_h / page_h
    left, right = box["l"] * sx, box["r"] * sx
    if "BOTTOM" in str(box.get("origin", "")).upper():
        top = (page_h - box["t"]) * sy
        bottom = (page_h - box["b"]) * sy
    else:
        top, bottom = box["t"] * sy, box["b"] * sy
    x0, x1 = sorted((left, right))
    y0, y1 = sorted((top, bottom))
    return PixelBox(int(x0), int(y0), int(x1), int(y1))
