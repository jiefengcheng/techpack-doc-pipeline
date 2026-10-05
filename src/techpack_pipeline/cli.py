from __future__ import annotations

import argparse
from pathlib import Path

from techpack_pipeline.pipeline import process_image


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Segment a tech-pack image into table, artwork crop, and drawing."
    )
    parser.add_argument("image", nargs="?", help="PNG/JPG tech-pack image")
    parser.add_argument("-o", "--output", default="runs", help="Output directory (default: ./runs)")
    parser.add_argument("--ui", action="store_true", help="Launch the Gradio demo")
    parser.add_argument("--no-drawing-ocr", action="store_true", help="Skip callout OCR on the drawing")
    parser.add_argument("--fast-tables", action="store_true", help="Use the faster table model")
    parser.add_argument("--no-translate", action="store_true", help="Skip glossary lock and residual translate")
    parser.add_argument("--no-residual", action="store_true", help="Glossary lock only; do not call a local model")
    parser.add_argument(
        "--localize-html",
        metavar="HTML",
        help="Translate an existing table.html without re-running layout/OCR",
    )
    parser.add_argument("--target", default="zh", help="Target language code (default: zh)")
    args = parser.parse_args()

    if args.ui:
        from techpack_pipeline.app import launch

        launch()
        return

    if args.localize_html:
        from techpack_pipeline.localize import localize_html_file

        dest = Path(args.output) if args.output != "runs" else None
        html_path, md_path, report = localize_html_file(
            args.localize_html,
            dest,
            target=args.target,
            residual=not args.no_residual,
        )
        print(f"bilingual: {html_path}")
        print(f"markdown:  {md_path}")
        print(
            "lock: "
            f"{report.glossary_hits} glossary, {report.frozen} frozen, "
            f"{report.residual} residual ({report.residual_engine})"
        )
        if report.leftovers:
            print("leftovers:")
            for item in report.leftovers:
                print(f"  - {item}")
        return

    if not args.image:
        parser.error("image is required unless --ui or --localize-html is set")

    result = process_image(
        args.image,
        args.output,
        ocr_drawing=not args.no_drawing_ocr,
        accurate_tables=not args.fast_tables,
        translate=not args.no_translate,
        residual=not args.no_residual,
        target_lang=args.target,
    )
    print(f"overlay:  {result.overlay_path}")
    print(f"table:    {result.table_html_path}")
    print(f"table md: {result.table_path}")
    if result.bilingual_html_path:
        print(f"bilingual:{result.bilingual_html_path}")
    print(f"artwork:  {result.artwork_path}")
    print(f"drawing:  {result.drawing_path}")
    if result.drawing_callouts:
        print("callouts:")
        for line in result.drawing_callouts:
            print(f"  - {line}")
    if result.localize:
        loc = result.localize
        print(
            "lock: "
            f"{loc.get('glossary_hits', 0)} glossary, {loc.get('frozen', 0)} frozen, "
            f"{loc.get('residual', 0)} residual ({loc.get('residual_engine', 'none')})"
        )
    print(f"manifest: {result.manifest_path}")
    print(f"wrote {Path(result.manifest_path).parent}")


if __name__ == "__main__":
    main()
