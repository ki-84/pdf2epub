from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Iterable

from pdf2epub.model import (
    Block,
    Chapter,
    Document,
    PageDir,
    RubyRun,
    TextRun,
    WritingMode,
)

# YomiToku paragraph.role -> internal Block role
ROLE_HEADING = {"section_headings", "title"}
ROLE_LIST = {"list_item", "index"}
ROLE_CAPTION = {"caption"}
ROLE_SKIP = {"page_header", "page_footer", "inline_formula", "display_formula"}

RUBY_PATTERN = re.compile(
    r"([一-鿿㐀-䶿豈-﫿々々]+)"
    r"[（(\[]([぀-ゟ゠-ヿー]+)[)）\]]"
)


def _paragraphs(page: dict[str, Any]) -> list[dict[str, Any]]:
    paras = page.get("paragraphs") or []
    return [p for p in paras if isinstance(p, dict)]


def _direction_of(p: dict[str, Any]) -> WritingMode | None:
    d = p.get("direction")
    if d in ("vertical", "horizontal"):
        return d  # type: ignore[return-value]
    return None


def detect_writing_mode(pages: Iterable[dict[str, Any]]) -> WritingMode:
    counter: Counter[str] = Counter()
    for page in pages:
        for p in _paragraphs(page):
            d = _direction_of(p)
            if d is not None:
                counter[d] += 1
    if not counter:
        return "horizontal"
    return "vertical" if counter["vertical"] > counter["horizontal"] else "horizontal"


def page_dir_for(mode: WritingMode) -> PageDir:
    return "rtl" if mode == "vertical" else "ltr"


def _classify_role(role: str | None) -> str | None:
    if role in ROLE_SKIP:
        return None
    if role in ROLE_HEADING:
        return "heading"
    if role in ROLE_LIST:
        return "list_item"
    if role in ROLE_CAPTION:
        return "caption"
    return "paragraph"


def _extract_rubies(text: str) -> tuple[str, list[RubyRun]]:
    """Extract inline-style ruby annotations like 漢字（かんじ） into RubyRun objects.

    Returns (cleaned_text, rubies). The cleaned_text keeps the base characters in
    place; the renderer decides how to wrap them with <ruby> elements based on
    the rubies list.
    """
    rubies: list[RubyRun] = []

    def _sub(m: re.Match[str]) -> str:
        base, ruby = m.group(1), m.group(2)
        rubies.append(RubyRun(base=base, ruby=ruby))
        return base

    cleaned = RUBY_PATTERN.sub(_sub, text)
    return cleaned, rubies


def _para_to_block(p: dict[str, Any], default_dir: WritingMode, *, keep_ruby: bool) -> Block | None:
    role = _classify_role(p.get("role"))
    if role is None:
        return None
    text = (p.get("contents") or "").strip()
    if not text:
        return None

    if keep_ruby:
        cleaned, rubies = _extract_rubies(text)
    else:
        cleaned, rubies = RUBY_PATTERN.sub(lambda m: m.group(1), text), []

    direction: WritingMode = _direction_of(p) or default_dir
    block_role = "heading" if role == "heading" else role  # type: ignore[assignment]
    level = 1 if role == "heading" else 0
    return Block(
        role=block_role,  # type: ignore[arg-type]
        level=level,
        runs=[TextRun(text=cleaned, rubies=rubies)],
        direction=direction,
    )


def _sorted_paragraphs(page: dict[str, Any]) -> list[dict[str, Any]]:
    paras = _paragraphs(page)
    if all(p.get("order") is not None for p in paras):
        return sorted(paras, key=lambda p: p.get("order", 0))
    return paras


def build_document(
    pages: list[dict[str, Any]],
    *,
    pdf_path: str,
    title: str | None = None,
    author: str = "",
    writing_mode: WritingMode | None = None,
    page_direction: PageDir | None = None,
    keep_ruby: bool = True,
    reverse_pages: bool = False,
) -> Document:
    mode = writing_mode or detect_writing_mode(pages)
    direction = page_direction or page_dir_for(mode)

    page_iter = list(reversed(pages)) if reverse_pages else pages

    blocks: list[Block] = []
    for page in page_iter:
        for p in _sorted_paragraphs(page):
            block = _para_to_block(p, default_dir=mode, keep_ruby=keep_ruby)
            if block is not None:
                blocks.append(block)

    chapters = _split_into_chapters(blocks)
    if not chapters:
        chapters = [Chapter(title="本文", blocks=[])]

    doc_title = title or os.path.splitext(os.path.basename(pdf_path))[0]
    return Document(
        title=doc_title,
        writing_mode=mode,
        page_direction=direction,
        chapters=chapters,
        source_pdf=pdf_path,
        author=author,
    )


def _split_into_chapters(blocks: list[Block]) -> list[Chapter]:
    chapters: list[Chapter] = []
    current: Chapter | None = None
    has_heading = any(b.role == "heading" for b in blocks)

    if not has_heading:
        if blocks:
            chapters.append(Chapter(title="本文", blocks=blocks))
        return chapters

    auto_index = 0
    for b in blocks:
        if b.role == "heading":
            title_text = b.runs[0].text if b.runs else ""
            if not title_text:
                auto_index += 1
                title_text = f"第{auto_index}章"
            current = Chapter(title=title_text, blocks=[b])
            chapters.append(current)
        else:
            if current is None:
                auto_index += 1
                current = Chapter(title="序", blocks=[])
                chapters.append(current)
            current.blocks.append(b)
    return chapters
