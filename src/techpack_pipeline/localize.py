"""Glossary lock, then residual translate of leftover prose.

Digits, units, and vendor codes stay frozen. The model never sees locked
terms. Artwork is not translated. Drawing callouts are not sent through
the BOM glossary.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from techpack_pipeline.table_reconstruct import _standalone_html, html_to_markdown

GLOSSARY_DIR = Path(__file__).resolve().parent / "data"
PLACEHOLDER = "⟦F{i}⟧"
TD_RE = re.compile(r"(<td[^>]*>)([\s\S]*?)(</td>)", re.I)
ZH_LINE = (
    "color:#1a1a1a;font-size:11px;font-weight:400;margin-top:3px;"
    "white-space:inherit;"
)
FROZEN_RE = re.compile(
    r"""
    (?:
        Micro-?Pak
      | YKK\s*[:#]?\s*\d+
      | \bYKK\b
      | \bN/?A\b
      | \#\s*\d+
      | \d+\s*/\s*\d+\s*"
      | \d+(?:\.\d+)?\s*(?:oz|yds?|mm|cm|in)\b
      | \d+(?:\.\d+)?\s*%
      | \d+(?:\.\d+)?\s*"\s*L\b
      | \d+(?:\.\d+)?\s*"
      | \d+(?:\.\d+)?
    )
    """,
    re.I | re.X,
)
FROM_HEM_RE = re.compile(
    r'(\d+(?:\s*/\s*\d+)?\s*")\s*From\s+Hem',
    re.I,
)
LATIN_CHUNK_RE = re.compile(
    r"[A-Za-z][A-Za-z'&./-]{1,}(?:\s+[A-Za-z][A-Za-z'&./-]{1,})*"
)
KEEP_EN = frozenset({"logo"})


@dataclass
class LocalizeReport:
    cells: int = 0
    changed: int = 0
    glossary_hits: int = 0
    frozen: int = 0
    residual: int = 0
    residual_engine: str = "none"
    target: str = "zh"
    leftovers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "cells": self.cells,
            "changed": self.changed,
            "glossary_hits": self.glossary_hits,
            "frozen": self.frozen,
            "residual": self.residual,
            "residual_engine": self.residual_engine,
            "target": self.target,
            "leftovers": self.leftovers,
        }


def load_glossary(target: str = "zh", path: str | Path | None = None) -> list[tuple[str, str]]:
    glossary_path = Path(path) if path else Path(
        os.environ.get("TECHPACK_GLOSSARY", GLOSSARY_DIR / f"glossary_en_{target}.json")
    )
    if not glossary_path.exists():
        return []
    raw = json.loads(glossary_path.read_text(encoding="utf-8"))
    pairs = [(str(src), str(dst)) for src, dst in raw.items() if src and dst]
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def freeze_spans(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def keep(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return PLACEHOLDER.format(i=len(held) - 1)

    return FROZEN_RE.sub(keep, text), held


def restore_spans(text: str, held: list[str]) -> str:
    for i, value in enumerate(held):
        text = text.replace(PLACEHOLDER.format(i=i), value)
    return text


def apply_glossary(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    hits = 0
    for src, dst in pairs:
        pattern = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)", re.I)
        text, n = pattern.subn(dst, text)
        hits += n
    return text, hits


def leftover_english(text: str) -> list[str]:
    out = []
    for chunk in LATIN_CHUNK_RE.findall(text):
        if "⟦F" in chunk:
            continue
        cleaned = chunk.strip(" .:/,-")
        if len(cleaned) < 3:
            continue
        if cleaned.lower() in KEEP_EN:
            continue
        if cleaned.replace(" ", "").isdecimal():
            continue
        out.append(cleaned)
    return out


def lock_cell(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int, int, list[str]]:
    text = text.strip()
    if not text:
        return "", 0, 0, []
    text = FROM_HEM_RE.sub(r"距脚口 \1", text)
    frozen_text, held = freeze_spans(text)
    locked, hits = apply_glossary(frozen_text, pairs)
    leftovers = leftover_english(locked)
    restored = restore_spans(locked, held)
    restored = re.sub(r"[ \t]{2,}", " ", restored).strip()
    restored = re.sub(r"\s+,", ",", restored)
    return restored, hits, len(held), leftovers


def _ollama_url() -> str:
    return os.environ.get("TECHPACK_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def _http_json(url: str, payload: dict | None = None, timeout: float = 8.0) -> dict | None:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _pick_ollama_model(tags: dict) -> str | None:
    forced = os.environ.get("TECHPACK_OLLAMA_MODEL")
    names = [m.get("name", "") for m in tags.get("models", []) if m.get("name")]
    if forced and forced in names:
        return forced
    if forced:
        return forced
    for name in names:
        if "qwen" in name.lower():
            return name
    return names[0] if names else None


def residual_engine() -> tuple[str, str | None]:
    if os.environ.get("TECHPACK_CLOUD_TRANSLATE") == "1" and os.environ.get("DEEPSEEK_API_KEY"):
        return "cloud", os.environ.get("TECHPACK_CLOUD_MODEL", "deepseek-chat")
    tags = _http_json(f"{_ollama_url()}/api/tags", timeout=0.4)
    if not tags:
        return "none", None
    model = _pick_ollama_model(tags)
    if not model:
        return "none", None
    return "local", model


def _translate_phrases(phrases: list[str], engine: str, model: str | None) -> dict[str, str]:
    unique = list(dict.fromkeys(phrases))
    if not unique or engine == "none" or not model:
        return {}
    mapped: dict[str, str] = {}
    for phrase in unique:
        translated = _translate_one(phrase, engine, model)
        if translated:
            mapped[phrase] = translated
    return mapped


def _translate_one(phrase: str, engine: str, model: str) -> str | None:
    prompt = (
        "Translate this garment-factory BOM phrase into Simplified Chinese. "
        "Copy any ⟦F#⟧ token unchanged. Do not translate numbers, units, or brand codes. "
        "Reply with only the translation.\n\n"
        f"{phrase}"
    )
    if engine == "local":
        body = _http_json(
            f"{_ollama_url()}/api/chat",
            {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0},
            },
            timeout=45.0,
        )
        if not body:
            return None
        text = ((body.get("message") or {}).get("content") or "").strip()
        return text or None
    if engine == "cloud":
        return _cloud_chat(prompt, model, os.environ.get("DEEPSEEK_API_KEY", ""))
    return None


def _cloud_chat(prompt: str, model: str, key: str) -> str | None:
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    url = os.environ.get("TECHPACK_CLOUD_URL", "https://api.deepseek.com/chat/completions")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    choices = body.get("choices") or []
    if not choices:
        return None
    text = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
    return text or None


def apply_residual(text: str, leftovers: list[str], translations: dict[str, str]) -> str:
    for src in sorted(leftovers, key=len, reverse=True):
        dst = translations.get(src)
        if not dst:
            continue
        text = re.sub(rf"(?<!\w){re.escape(src)}(?!\w)", dst, text)
    return text


def _bilingual_inner(en: str, zh: str) -> str:
    en_esc = html.escape(en)
    if not zh or zh == en:
        return en_esc
    return (
        f"<div>{en_esc}</div>"
        f'<div data-tp-zh="1" style="{ZH_LINE}">{html.escape(zh)}</div>'
    )


def _plain_cell(inner: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", inner, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def localize_table_html(
    fragment: str,
    *,
    target: str = "zh",
    residual: bool = True,
    glossary_path: str | Path | None = None,
) -> tuple[str, LocalizeReport]:
    report = LocalizeReport(target=target)
    pairs = load_glossary(target, glossary_path)
    cells: list[tuple[re.Match[str], str, str, int, int, list[str]]] = []
    leftovers: list[str] = []

    for match in TD_RE.finditer(fragment):
        inner = match.group(2)
        if 'data-tp-zh="' in inner:
            continue
        source = _plain_cell(inner)
        report.cells += 1
        if not source:
            continue
        locked, hits, frozen_n, cell_left = lock_cell(source, pairs)
        report.glossary_hits += hits
        report.frozen += frozen_n
        leftovers.extend(cell_left)
        cells.append((match, source, locked, hits, frozen_n, cell_left))

    engine, model = ("none", None)
    translations: dict[str, str] = {}
    if residual:
        engine, model = residual_engine()
        translations = _translate_phrases(leftovers, engine, model)
    report.residual_engine = engine
    report.leftovers = list(dict.fromkeys(leftovers))
    report.residual = len(translations)

    pieces: list[str] = []
    cursor = 0
    for match, source, locked, _hits, _frozen_n, cell_left in cells:
        zh = apply_residual(locked, cell_left, translations) if translations else locked
        if zh != source:
            report.changed += 1
        inner = _bilingual_inner(source, zh)
        pieces.append(fragment[cursor : match.start()])
        pieces.append(f"{match.group(1)}{inner}{match.group(3)}")
        cursor = match.end()
    pieces.append(fragment[cursor:])
    return "".join(pieces), report


def save_bilingual_outputs(fragment: str, dest_dir: Path) -> tuple[Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    html_path = dest_dir / "table_bilingual.html"
    md_path = dest_dir / "table_bilingual.md"
    html_path.write_text(_standalone_html(fragment), encoding="utf-8")
    md_path.write_text(_bilingual_markdown(fragment), encoding="utf-8")
    return html_path, md_path


def _bilingual_markdown(fragment: str) -> str:
    patched = re.sub(
        r'<div data-tp-zh="1"[^>]*>',
        " / ",
        fragment,
        flags=re.I,
    )
    return html_to_markdown(patched)


def localize_html_file(
    source: str | Path,
    dest_dir: str | Path | None = None,
    *,
    target: str = "zh",
    residual: bool = True,
) -> tuple[Path, Path, LocalizeReport]:
    src = Path(source).expanduser().resolve()
    out_dir = Path(dest_dir).expanduser().resolve() if dest_dir else src.parent
    fragment = src.read_text(encoding="utf-8")
    bilingual, report = localize_table_html(fragment, target=target, residual=residual)
    html_path, md_path = save_bilingual_outputs(bilingual, out_dir)
    (out_dir / "localize.json").write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return html_path, md_path, report
