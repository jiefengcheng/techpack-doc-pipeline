from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, TableFormerMode
from docling.document_converter import DocumentConverter, ImageFormatOption

from techpack_pipeline.classify import pick_artwork_index, picture_kind
from techpack_pipeline.geometry import PixelBox, page_box_to_pixels
from techpack_pipeline.table_reconstruct import reconstruct_table_html, save_table_outputs
from techpack_pipeline.visualize import draw_overlay

_CONVERTER: DocumentConverter | None = None


@dataclass
class Region:
    kind: str
    box: tuple[int, int, int, int]
    path: str | None = None
    text: str = ""


@dataclass
class PipelineResult:
    source: str
    overlay_path: str
    table_html: str
    table_markdown: str
    table_html_path: str | None
    table_path: str | None
    table_image_path: str | None
    artwork_path: str | None
    drawing_path: str | None
    drawing_callouts: list[str] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    manifest_path: str = ""
    bilingual_html: str = ""
    bilingual_html_path: str | None = None
    bilingual_markdown_path: str | None = None
    localize_path: str | None = None
    localize: dict = field(default_factory=dict)


def _item_label(item) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return item.__class__.__name__
    return getattr(label, "value", None) or str(label)


def _prov_boxes(item) -> list[dict]:
    boxes = []
    for prov in getattr(item, "prov", []) or []:
        bbox = getattr(prov, "bbox", None)
        if bbox is None:
            continue
        boxes.append(
            {
                "l": float(bbox.l),
                "t": float(bbox.t),
                "r": float(bbox.r),
                "b": float(bbox.b),
                "origin": str(getattr(bbox, "coord_origin", "BOTTOMLEFT")),
            }
        )
    return boxes


def _prepare_image(path: Path, min_short_side: int = 1400) -> Image.Image:
    image = Image.open(path).convert("RGB")
    short = min(image.size)
    if short < min_short_side:
        scale = min_short_side / short
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    return image


def _ocr_options():
    if sys.platform == "darwin":
        try:
            from docling.datamodel.pipeline_options import OcrMacOptions

            return OcrMacOptions(lang=["en-US"], scale=1.5)
        except Exception:
            pass
    return RapidOcrOptions()


def get_converter(accurate_tables: bool = True) -> DocumentConverter:
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER
    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE if accurate_tables else TableFormerMode.FAST
    opts.ocr_options = _ocr_options()
    _CONVERTER = DocumentConverter(
        allowed_formats=[InputFormat.IMAGE],
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)},
    )
    return _CONVERTER


def _ocr_drawing(crop_path: Path) -> list[str]:
    if sys.platform != "darwin":
        return []
    try:
        from ocrmac import ocrmac
    except ImportError:
        return []
    rows = ocrmac.OCR(str(crop_path), recognition_level="accurate").recognize()
    texts: list[str] = []
    for row in rows:
        text = str(row[0]).strip() if row else ""
        if text:
            texts.append(text)
    return texts


def process_image(
    source: str | Path,
    output_dir: str | Path,
    *,
    ocr_drawing: bool = True,
    accurate_tables: bool = True,
    translate: bool = True,
    residual: bool = True,
    target_lang: str = "zh",
) -> PipelineResult:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    out_dir = Path(output_dir).expanduser().resolve() / source_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    image = _prepare_image(source_path)
    work_png = out_dir / "page.png"
    image.save(work_png)

    result = get_converter(accurate_tables=accurate_tables).convert(str(work_png))
    doc = result.document

    pages = getattr(doc, "pages", {})
    page = pages[1] if 1 in pages else next(iter(pages.values()))
    page_w = float(getattr(page.size, "width", 0) or image.width)
    page_h = float(getattr(page.size, "height", 0) or image.height)

    regions: list[Region] = []
    picture_crops: list[tuple[PixelBox, Image.Image]] = []

    for item, _level in doc.iterate_items():
        label = _item_label(item).lower()
        boxes = _prov_boxes(item)
        if not boxes:
            continue
        box = page_box_to_pixels(boxes[0], page_w, page_h, image.width, image.height).clamp(
            image.width, image.height, pad=6
        )
        if label == "table":
            regions.append(Region(kind="table", box=(box.x0, box.y0, box.x1, box.y1)))
        elif label in {"picture", "figure"}:
            crop = image.crop((box.x0, box.y0, box.x1, box.y1))
            picture_crops.append((box, crop))

    artwork_path = None
    drawing_path = None
    callouts: list[str] = []
    artwork_idx = pick_artwork_index([crop for _, crop in picture_crops])

    for idx, (box, crop) in enumerate(picture_crops):
        kind = "artwork" if idx == artwork_idx else picture_kind(crop)
        if kind == "artwork" and artwork_path is None:
            dest = out_dir / "artwork.png"
            crop.save(dest)
            artwork_path = str(dest)
            regions.append(Region(kind="artwork", box=(box.x0, box.y0, box.x1, box.y1), path=artwork_path))
            continue
        dest = out_dir / ("drawing.png" if drawing_path is None else f"drawing_{idx}.png")
        crop.save(dest)
        if drawing_path is None:
            drawing_path = str(dest)
        if ocr_drawing:
            callouts.extend(_ocr_drawing(dest))
        regions.append(
            Region(
                kind="drawing",
                box=(box.x0, box.y0, box.x1, box.y1),
                path=str(dest),
                text="\n".join(callouts) if dest.name == "drawing.png" else "",
            )
        )

    if callouts:
        (out_dir / "drawing_callouts.txt").write_text("\n".join(callouts), encoding="utf-8")

    table_html, table_image_path = _reconstruct_table(image, regions, doc, out_dir)
    html_path, md_path = save_table_outputs(table_html, out_dir)
    table_md = md_path.read_text(encoding="utf-8")
    for region in regions:
        if region.kind == "table":
            region.path = str(html_path)

    bilingual_html = ""
    bilingual_html_path = None
    bilingual_md_path = None
    localize_path = None
    localize_report: dict = {}
    if translate and table_html:
        from techpack_pipeline.localize import localize_table_html, save_bilingual_outputs

        bilingual_html, report = localize_table_html(
            table_html, target=target_lang, residual=residual
        )
        localize_report = report.as_dict()
        bi_html, bi_md = save_bilingual_outputs(bilingual_html, out_dir)
        bilingual_html_path = str(bi_html)
        bilingual_md_path = str(bi_md)
        localize_path = str(out_dir / "localize.json")
        Path(localize_path).write_text(
            json.dumps(localize_report, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    overlay = draw_overlay(image, regions)
    overlay_path = out_dir / "overlay.png"
    overlay.save(overlay_path)

    result_obj = PipelineResult(
        source=str(source_path),
        overlay_path=str(overlay_path),
        table_html=table_html,
        table_markdown=table_md,
        table_html_path=str(html_path),
        table_path=str(md_path),
        table_image_path=table_image_path,
        artwork_path=artwork_path,
        drawing_path=drawing_path,
        drawing_callouts=callouts,
        regions=regions,
        manifest_path=str(out_dir / "manifest.json"),
        bilingual_html=bilingual_html,
        bilingual_html_path=bilingual_html_path,
        bilingual_markdown_path=bilingual_md_path,
        localize_path=localize_path,
        localize=localize_report,
    )
    payload = asdict(result_obj)
    payload.pop("table_html", None)
    payload.pop("bilingual_html", None)
    (out_dir / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return result_obj


def _reconstruct_table(image: Image.Image, regions: list[Region], doc, out_dir: Path) -> tuple[str, str | None]:
    table_box = next((r.box for r in regions if r.kind == "table"), None)
    if table_box is None:
        table_box = (0, 0, image.width, image.height)
    crop = image.crop(table_box)
    table_image_path = out_dir / "table.png"
    crop.save(table_image_path)
    try:
        html_doc = reconstruct_table_html(crop)
    except Exception:
        html_doc = ""
    if not html_doc:
        try:
            html_doc = doc.export_to_html()
        except Exception:
            html_doc = f"<pre>{doc.export_to_markdown()}</pre>"
    return html_doc, str(table_image_path)
