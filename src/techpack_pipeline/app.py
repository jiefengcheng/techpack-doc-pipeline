from __future__ import annotations

from pathlib import Path

import gradio as gr

from techpack_pipeline.pipeline import process_image

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "runs"

APP_CSS = """
#techpack-table {
  background: #ffffff !important;
  color: #111111 !important;
  padding: 8px 0 16px;
}
#techpack-table table {
  background: #ffffff !important;
  color: #111111 !important;
}
#techpack-table td, #techpack-table th {
  color: #111111 !important;
  border-color: #222222 !important;
}
#techpack-table [data-tp-zh] {
  color: #1a1a1a !important;
  font-weight: 400 !important;
  font-size: 11px !important;
}
"""


def _as_path(upload) -> str:
    if upload is None:
        raise ValueError("Upload a tech-pack PNG or JPG.")
    if isinstance(upload, str):
        return upload
    if isinstance(upload, dict):
        return upload.get("path") or upload.get("name")
    return str(upload)


def run_demo(upload, ocr_drawing: bool, translate: bool):
    source = _as_path(upload)
    result = process_image(source, OUTPUT_DIR, ocr_drawing=ocr_drawing, translate=translate)
    callouts = "\n".join(result.drawing_callouts) if result.drawing_callouts else "(none)"
    sheet = result.bilingual_html or result.table_html
    table_file = result.bilingual_html_path or result.table_html_path
    return (
        result.overlay_path,
        sheet,
        result.table_image_path,
        result.artwork_path,
        result.drawing_path,
        callouts,
        table_file,
        result.manifest_path,
    )


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Tech-pack layout demo", theme=gr.themes.Soft(), css=APP_CSS) as demo:
        gr.Markdown(
            """
            # Tech-pack layout demo
            Upload any screenshot-style tech pack. The pipeline segments the page into
            **table** (formed HTML spreadsheet), **artwork** (crop only), and **drawing** (optional callouts).
            Translation is glossary-first (EN→CN), then leftover prose if a local model is running.
            """
        )
        with gr.Row():
            with gr.Column(scale=1):
                upload = gr.File(label="Tech-pack image", file_types=[".png", ".jpg", ".jpeg", ".webp"])
                ocr_drawing = gr.Checkbox(label="OCR drawing callouts", value=True)
                translate = gr.Checkbox(label="Translate table EN→CN", value=True)
                btn = gr.Button("Process", variant="primary")
                table_file = gr.File(label="Table HTML")
                manifest = gr.File(label="Run manifest")
            with gr.Column(scale=2):
                overlay = gr.Image(label="Layout overlay", type="filepath")
        gr.Markdown("### Table (EN + CN)")
        table = gr.HTML(elem_id="techpack-table")
        table_image = gr.Image(label="Original table crop", type="filepath")
        with gr.Row():
            artwork = gr.Image(label="Artwork crop", type="filepath")
            drawing = gr.Image(label="Drawing crop", type="filepath")
        callouts = gr.Textbox(label="Drawing callouts", lines=8)
        btn.click(
            fn=run_demo,
            inputs=[upload, ocr_drawing, translate],
            outputs=[overlay, table, table_image, artwork, drawing, callouts, table_file, manifest],
        )
    return demo


def launch() -> None:
    build_ui().launch(server_name="0.0.0.0", server_port=7861, share=False)


if __name__ == "__main__":
    launch()
