"""Reconstruct a ruled table as a viewable HTML spreadsheet.

Header and BOM use different column lines (as on the printed page).
Cell text comes from Apple Vision / RapidOCR. Spanning OCR lines are
split on the ruling lines so values stay in the right columns.

HTML uses inline styles so Gradio dark theme cannot wash out the grid.
"""

from __future__ import annotations

import html
import io
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

SHEET = (
    "background:#ffffff;color:#111111;padding:12px;overflow:auto;"
    "border:1px solid #111111;border-radius:0;"
)
TABLE = (
    "border-collapse:collapse;width:max-content;min-width:1080px;"
    "background:#ffffff;color:#111111;"
    "font:12px/1.35 ui-sans-serif,system-ui,sans-serif;"
    "margin:0;"
)
TD = (
    "border:1px solid #222222;padding:5px 8px;vertical-align:middle;"
    "background:#ffffff;color:#111111;white-space:nowrap;"
)
TD_WRAP = TD.replace("white-space:nowrap;", "white-space:normal;min-width:240px;max-width:360px;")
TD_HEAD = TD + "font-weight:650;background:#efefef;"
TD_TITLE = TD_HEAD + "text-align:left;"


def reconstruct_table_html(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    items = [_tidy_item(t, b) for t, b in _ocr_items(rgb)]
    raw = _line_grid_html(rgb, items) or _img2table_html(rgb, items) or _rapid_html(rgb, items)
    if not raw:
        return ""
    return _wrap_html(_clean_html(raw))


def _line_grid_html(
    image: Image.Image,
    items: list[tuple[str, tuple[float, float, float, float]]],
) -> str:
    try:
        import cv2
    except ImportError:
        return ""

    gray = np.array(image.convert("L"))
    height, width = gray.shape
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 8
    )
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 25, 40), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(height // 30, 28)))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    ys = _line_positions(horiz, axis=1, frac=0.18)
    if len(ys) < 4:
        return ""

    desc_y = _description_y(items)
    if desc_y is None:
        desc_y = ys[min(3, len(ys) - 1)]

    header_ys = [y for y in ys if y <= desc_y + 8]
    bom_ys = [y for y in ys if y >= desc_y - 8]
    if len(header_ys) < 2:
        header_ys = ys[:3]
    if len(bom_ys) < 3:
        bom_ys = ys[max(0, len(ys) - 16) :]
    if header_ys[-1] < bom_ys[0]:
        bom_ys = [header_ys[-1], *bom_ys]
    elif header_ys[-1] != bom_ys[0]:
        header_ys = [y for y in header_ys if y <= bom_ys[0]]
        if header_ys[-1] != bom_ys[0]:
            header_ys.append(bom_ys[0])

    xs_header = _merge_close(_band_xs(vert, header_ys[0], header_ys[-1], frac=0.12), 70)
    xs_bom = _merge_close(_band_xs(vert, bom_ys[0], bom_ys[-1], frac=0.22), 50)
    if len(xs_bom) < 3:
        xs_bom = _merge_close(_line_positions(vert, axis=0, frac=0.25), 50)
    if len(xs_header) < 3:
        xs_header = xs_bom
    if len(xs_bom) < 3 or len(bom_ys) < 3:
        return ""

    header_items = [it for it in items if (it[1][1] + it[1][3]) / 2 < desc_y - 4]
    bom_items = _split_spanning(
        image,
        [it for it in items if (it[1][1] + it[1][3]) / 2 >= desc_y - 4],
        xs_bom,
    )
    header_grid = _fill_grid(header_items, xs_header, header_ys)
    bom_grid = _fill_grid(bom_items, xs_bom, bom_ys)
    header_grid, xs_header = _drop_empty_edge_cols(header_grid, xs_header)
    header_grid = [_dedupe_cells(row) for row in header_grid if any(row)]
    bom_grid = [_dedupe_cells(row) for row in bom_grid if any(row)]
    if _filled_count(bom_grid) < 4:
        return ""

    parts = [f'<div style="{SHEET}">']
    if header_grid:
        parts.append(_emit_table(header_grid, xs_header, title_rows=True))
    parts.append(_emit_table(bom_grid, xs_bom, title_rows=False, wrap_first=True))
    parts.append("</div>")
    return "".join(parts)


def _band_xs(vert: np.ndarray, y0: int, y1: int, frac: float) -> list[int]:
    band = vert[max(0, y0) : max(y0 + 1, y1), :]
    if band.size == 0:
        return []
    return _line_positions(band, axis=0, frac=frac)


def _merge_close(xs: list[int], min_gap: int) -> list[int]:
    if not xs:
        return xs
    out = [xs[0]]
    for x in xs[1:-1]:
        if x - out[-1] >= min_gap:
            out.append(x)
    if xs[-1] - out[-1] >= min_gap:
        out.append(xs[-1])
    elif len(out) == 1:
        out.append(xs[-1])
    else:
        out[-1] = xs[-1]
    return out


def _description_y(items: list[tuple[str, tuple[float, float, float, float]]]) -> float | None:
    for text, (_x1, y1, _x2, y2) in items:
        if text.strip().lower() == "description":
            return (y1 + y2) / 2
    return None


def _fill_grid(
    items: list[tuple[str, tuple[float, float, float, float]]],
    xs: list[int],
    ys: list[int],
) -> list[list[str]]:
    cols, rows = len(xs) - 1, len(ys) - 1
    buckets: list[list[list[tuple[float, float, str]]]] = [
        [[] for _ in range(cols)] for _ in range(rows)
    ]
    for text, (x1, y1, x2, y2) in items:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ci, ri = _slot(cx, xs), _slot(cy, ys)
        if ci is None or ri is None:
            continue
        buckets[ri][ci].append((y1, x1, text))
    return [[" ".join(t for _, _, t in sorted(cell)) for cell in row] for row in buckets]


def _split_spanning(
    image: Image.Image,
    items: list[tuple[str, tuple[float, float, float, float]]],
    xs: list[int],
) -> list[tuple[str, tuple[float, float, float, float]]]:
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for text, box in items:
        x1, y1, x2, y2 = box
        covered = [i for i in range(len(xs) - 1) if x1 < xs[i + 1] - 3 and x2 > xs[i] + 3]
        if len(covered) <= 1:
            out.append((text, box))
            continue
        widths = [min(x2, xs[i + 1]) - max(x1, xs[i]) for i in covered]
        if max(widths) / max(x2 - x1, 1) > 0.72:
            out.append((text, box))
            continue
        for i in covered:
            slice_box = (float(xs[i]), y1, float(xs[i + 1]), y2)
            crop = image.crop((int(xs[i]), int(max(0, y1 - 2)), int(xs[i + 1]), int(y2 + 2)))
            if _is_blank(crop):
                continue
            sliced = _ocr_items(crop)
            if not sliced:
                continue
            for st, (sx1, sy1, sx2, sy2) in sliced:
                out.append((st, (sx1 + xs[i], sy1 + max(0, y1 - 2), sx2 + xs[i], sy2 + max(0, y1 - 2))))
    return out


def _is_blank(crop: Image.Image) -> bool:
    if crop.width < 2 or crop.height < 2:
        return True
    arr = np.asarray(crop.convert("L"))
    return float((arr < 240).mean()) < 0.012


def _filled_count(grid: list[list[str]]) -> int:
    return sum(1 for row in grid for cell in row if cell)


def _drop_empty_edge_cols(grid: list[list[str]], xs: list[int]) -> tuple[list[list[str]], list[int]]:
    if not grid:
        return grid, xs
    lo, hi = 0, len(grid[0]) - 1
    while lo < hi and all(not row[lo] for row in grid):
        lo += 1
    while hi > lo and all(not row[hi] for row in grid):
        hi -= 1
    return [row[lo : hi + 1] for row in grid], xs[lo : hi + 2]


def _dedupe_cells(row: list[str]) -> list[str]:
    cleaned = []
    for cell in row:
        parts: list[str] = []
        for part in cell.split():
            if parts and parts[-1].lower() == part.lower():
                continue
            parts.append(part)
        n = len(parts)
        if n >= 2 and n % 2 == 0 and [p.lower() for p in parts[: n // 2]] == [p.lower() for p in parts[n // 2 :]]:
            parts = parts[: n // 2]
        cleaned.append(" ".join(parts))
    return cleaned


def _emit_table(
    grid: list[list[str]],
    xs: list[int],
    *,
    title_rows: bool,
    wrap_first: bool = False,
) -> str:
    cols = len(xs) - 1
    widths = _col_widths(xs)
    parts = [f'<table style="{TABLE}">', "<colgroup>"]
    for pct in widths:
        parts.append(f'<col style="width:{pct:.1f}%">')
    parts.append("</colgroup>")
    for ri, row in enumerate(grid):
        nonempty = [i for i, t in enumerate(row) if t]
        parts.append("<tr>")
        if nonempty == [0]:
            parts.append(f'<td colspan="{cols}" style="{TD_TITLE}">{html.escape(row[0])}</td>')
        else:
            cells = _merge_color_details(row)
            for ci, (text, span) in enumerate(cells):
                style = TD_HEAD if title_rows or (wrap_first and ri == 0) else TD
                if wrap_first and ri > 0 and ci == 0:
                    style = TD_WRAP
                span_attr = f' colspan="{span}"' if span > 1 else ""
                parts.append(f'<td{span_attr} style="{style}">{html.escape(text)}</td>')
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _col_widths(xs: list[int]) -> list[float]:
    spans = [max(xs[i + 1] - xs[i], 1) for i in range(len(xs) - 1)]
    total = sum(spans)
    return [100.0 * s / total for s in spans]


def _merge_color_details(texts: list[str]) -> list[tuple[str, int]]:
    skip = set()
    spans = [1] * len(texts)
    copied = list(texts)
    for i, text in enumerate(texts):
        if text.lower() != "color details":
            continue
        lo, hi = i, i
        while lo > 0 and not texts[lo - 1]:
            lo -= 1
        while hi + 1 < len(texts) and not texts[hi + 1]:
            hi += 1
        spans[lo] = hi - lo + 1
        copied[lo] = text
        skip.update(range(lo + 1, hi + 1))
    return [(copied[i], spans[i]) for i in range(len(copied)) if i not in skip]


def _line_positions(mask: np.ndarray, axis: int, frac: float, gap: int = 4) -> list[int]:
    profile = (mask > 0).sum(axis=axis)
    thresh = max(float(profile.max()) * frac, 10.0)
    hits = np.where(profile > thresh)[0]
    groups: list[list[int]] = []
    for pos in hits.tolist():
        if not groups or pos - groups[-1][-1] > gap:
            groups.append([pos])
        else:
            groups[-1].append(pos)
    return [int(np.median(group)) for group in groups]


def _slot(value: float, edges: list[int]) -> int | None:
    if not edges:
        return None
    if value < edges[0]:
        return 0
    if value >= edges[-1]:
        return len(edges) - 2
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return None


def _tidy_item(
    text: str, box: tuple[float, float, float, float]
) -> tuple[str, tuple[float, float, float, float]]:
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"60\*+", '60"', text)
    text = re.sub(r"1/8\S{0,3}", '1/8"', text)
    text = re.sub(r"18[^\x00-\x7F]+L\b", '18" L', text)
    text = re.sub(r"^Lach$", "Each", text, flags=re.I)
    text = re.sub(r"4[^\x00-\x7F]+From", '4" From', text)
    return text, box


def _img2table_html(
    image: Image.Image,
    items: list[tuple[str, tuple[float, float, float, float]]],
) -> str:
    try:
        from img2table.document import Image as I2TImage
    except ImportError:
        return ""

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    try:
        tables = I2TImage(src=buf).extract_tables()
    except Exception:
        return ""
    if not tables:
        return ""

    table = max(tables, key=lambda t: _table_area(t))
    _fill_cells(table, items)
    html_doc = (getattr(table, "html", None) or "").strip()
    if "<td" not in html_doc.lower():
        return ""
    return _wrap_html(html_doc)


def _table_area(table) -> int:
    box = getattr(table, "bbox", None)
    if box is None:
        return 0
    return max(0, int(box.x2) - int(box.x1)) * max(0, int(box.y2) - int(box.y1))


def _fill_cells(table, items: list[tuple[str, tuple[float, float, float, float]]]) -> None:
    groups: dict[tuple[int, int, int, int], list] = defaultdict(list)
    for row in table.content.values():
        for cell in row:
            box = cell.bbox
            key = (int(box.x1), int(box.y1), int(box.x2), int(box.y2))
            groups[key].append(cell)

    assigned: dict[tuple[int, int, int, int], list[tuple[float, float, str]]] = {
        key: [] for key in groups
    }
    for text, box in items:
        best_key, best_score = None, None
        ocr_area = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
        for key in groups:
            ov = _overlap(box, key)
            if ov <= 0:
                continue
            cell_area = max((key[2] - key[0]) * (key[3] - key[1]), 1)
            score = (ov / ocr_area, -cell_area)
            if best_score is None or score > best_score:
                best_score, best_key = score, key
        if best_key is None or best_score[0] < 0.25:
            continue
        assigned[best_key].append((box[1], box[0], text))

    for key, cells in groups.items():
        texts = [t for _, _, t in sorted(assigned[key])]
        value = " ".join(texts) if texts else None
        for cell in cells:
            cell.value = value


def _overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _ocr_items(image: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
    return _ocrmac_recs(image) or _rapidocr_recs(image) or []


def _ocrmac_recs(image: Image.Image):
    if sys.platform != "darwin":
        return None
    try:
        from ocrmac import ocrmac
    except ImportError:
        return None
    recs = ocrmac.OCR(
        image, language_preference=["en-US"], recognition_level="accurate"
    ).recognize(px=True)
    items = []
    for text, _conf, bbox in recs or []:
        text = (text or "").strip()
        if not text:
            continue
        x1, y1, x2, y2 = (float(v) for v in bbox)
        items.append((text, (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))))
    return items or None


def _rapidocr_recs(image: Image.Image):
    try:
        from rapidocr import RapidOCR
    except ImportError:
        return None
    res = RapidOCR()(np.array(image.convert("RGB")))
    if res is None or getattr(res, "boxes", None) is None:
        return None
    items = []
    for box, text in zip(res.boxes, res.txts):
        text = (text or "").strip()
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        items.append((text, (min(xs), min(ys), max(xs), max(ys))))
    return items or None


@lru_cache(maxsize=1)
def _table_engine():
    from rapid_table import ModelType, RapidTable, RapidTableInput

    return RapidTable(RapidTableInput(model_type=ModelType.SLANETPLUS, use_ocr=True))


def _rapid_html(
    image: Image.Image,
    items: list[tuple[str, tuple[float, float, float, float]]],
) -> str:
    if not items:
        return ""
    boxes, texts, scores = [], [], []
    for text, (x1, y1, x2, y2) in items:
        boxes.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        texts.append(text)
        scores.append(0.9)
    ocr = (np.array(boxes, dtype=np.float32), tuple(texts), tuple(scores))
    try:
        from rapid_table.utils.utils import format_ocr_results

        engine = _table_engine()
        arr = np.array(image.convert("RGB"))
        imgs = engine._load_imgs([arr])
        pred_structures, cell_bboxes = engine.table_structure(imgs)
        dt_boxes, rec_res = format_ocr_results(ocr, arr.shape[0], arr.shape[1])
        return engine.table_matcher.process_one(
            pred_structures[0], cell_bboxes[0], dt_boxes, rec_res
        )
    except Exception:
        return ""


def _clean_html(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"</?body[^>]*>", "", raw, flags=re.I)
    raw = re.sub(r"</?html[^>]*>", "", raw, flags=re.I)
    if "<table" not in raw.lower() and "<div" not in raw.lower():
        raw = f"<table>{raw}</table>"
    return raw


def _wrap_html(table_html: str) -> str:
    if "background:#ffffff" in table_html or "background:#fff" in table_html:
        return table_html
    return f'<div style="{SHEET}">{table_html}</div>'


def html_to_markdown(wrapped_html: str) -> str:
    tables = re.findall(r"<table[\s\S]*?</table>", wrapped_html, flags=re.I)
    if not tables:
        return ""
    lines_out = []
    for table in tables:
        rows = []
        for tr in re.findall(r"<tr[\s\S]*?</tr>", table, flags=re.I):
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", tr, flags=re.I)
            rows.append([_cell_text(c) for c in cells])
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        header, body = rows[0], rows[1:]
        lines_out.append("| " + " | ".join(header) + " |")
        lines_out.append("| " + " | ".join("---" for _ in header) + " |")
        for row in body:
            lines_out.append("| " + " | ".join(row) + " |")
        lines_out.append("")
    return "\n".join(lines_out).strip()


def _cell_text(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("|", "/").strip()


def save_table_outputs(html_doc: str, dest_dir: Path) -> tuple[Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    html_path = dest_dir / "table.html"
    md_path = dest_dir / "table.md"
    html_path.write_text(_standalone_html(html_doc), encoding="utf-8")
    md_path.write_text(html_to_markdown(html_doc), encoding="utf-8")
    return html_path, md_path


def _fragment_only(html_doc: str) -> str:
    text = html_doc.strip()
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<head[\s\S]*?</head>", "", text, flags=re.I)
    text = re.sub(r"</?html[^>]*>", "", text, flags=re.I)
    text = re.sub(r"</?body[^>]*>", "", text, flags=re.I)
    return text.strip()


def _standalone_html(fragment: str) -> str:
    inner = _fragment_only(fragment)
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Tech-pack table</title>"
        "<style>body{margin:16px;background:#f5f5f5}</style>"
        "</head><body>"
        f"{inner}</body></html>"
    )
