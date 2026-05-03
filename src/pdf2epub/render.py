from __future__ import annotations

import re
from xml.sax.saxutils import escape

from pdf2epub.model import Block, Chapter, Document, RubyRun, TextRun

# Yomitoku frequently misreads ASCII '-' as the long-vowel mark 'ー' next to
# digits (e.g. "ー1000/500=ー2"). Restore the minus sign in numeric contexts.
_FALSE_MINUS_RE = re.compile(r"(?<=[=()\s\d/])ー(?=\d)|ー(?=\d)|(?<=\d)ー(?=[=)])")


def _fix_text(text: str, direction: str) -> str:
    text = _FALSE_MINUS_RE.sub("-", text)
    # Collapse the original PDF column wrap. Vertical Japanese text reads as
    # one continuous stream; in horizontal text, replace wrap with a space so
    # English words don't run together.
    if direction == "vertical":
        text = text.replace("\n", "")
    else:
        text = re.sub(r"\s*\n\s*", " ", text)
    return text


XHTML_TEMPLATE = (
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
    "<head>\n"
    '  <meta charset="utf-8" />\n'
    "  <title>{title}</title>\n"
    '  <link rel="stylesheet" type="text/css" href="style.css" />\n'
    "</head>\n"
    "<body>\n"
    "{body}"
    "</body>\n"
    "</html>\n"
)


def _render_run_with_rubies(run: TextRun, direction: str = "vertical") -> str:
    text = _fix_text(run.text, direction)
    if not run.rubies:
        return escape(text)
    out: list[str] = []
    cursor = 0
    for ruby in run.rubies:
        idx = text.find(ruby.base, cursor)
        if idx < 0:
            continue
        if idx > cursor:
            out.append(escape(text[cursor:idx]))
        out.append(_ruby_html(ruby))
        cursor = idx + len(ruby.base)
    if cursor < len(text):
        out.append(escape(text[cursor:]))
    return "".join(out)


def _ruby_html(ruby: RubyRun) -> str:
    return (
        "<ruby>"
        f"<rb>{escape(ruby.base)}</rb>"
        f"<rp>(</rp><rt>{escape(ruby.ruby)}</rt><rp>)</rp>"
        "</ruby>"
    )


def _block_attrs(block: Block, doc_writing_mode: str) -> str:
    """When a block's direction differs from the document's writing mode,
    pin the block to its own writing mode so figure captions, sidenotes,
    formulas etc. render in the right orientation."""
    if block.direction and block.direction != doc_writing_mode:
        if block.direction == "horizontal":
            return ' class="block-horizontal"'
        if block.direction == "vertical":
            return ' class="block-vertical"'
    return ""


def _render_block(block: Block, doc_writing_mode: str = "vertical") -> str:
    if block.role == "figure":
        return _render_figure(block)
    inner = "".join(
        _render_run_with_rubies(r, block.direction or doc_writing_mode)
        for r in block.runs
    )
    attrs = _block_attrs(block, doc_writing_mode)
    if block.role == "heading":
        level = max(1, min(block.level or 1, 6))
        return f"<h{level}{attrs}>{inner}</h{level}>\n"
    if block.role == "list_item":
        return f"<ul{attrs}><li>{inner}</li></ul>\n"
    if block.role == "caption":
        return f'<p class="no-indent"{attrs}><em>{inner}</em></p>\n'
    return f"<p{attrs}>{inner}</p>\n"


def _render_figure(block: Block) -> str:
    href = block.image_href or ""
    caption = "".join(_render_run_with_rubies(r) for r in block.runs).strip()
    img = f'<img src="{escape(href, {chr(34): "&quot;"})}" alt="figure" />'
    if caption:
        return (
            '<figure class="figure">\n'
            f"  {img}\n"
            f"  <figcaption>{caption}</figcaption>\n"
            "</figure>\n"
        )
    return f'<figure class="figure">{img}</figure>\n'


def render_chapter_xhtml(
    chapter: Chapter, *, language: str, doc_writing_mode: str = "vertical"
) -> str:
    body = "".join(_render_block(b, doc_writing_mode) for b in chapter.blocks)
    return XHTML_TEMPLATE.format(
        lang=escape(language, {'"': "&quot;"}),
        title=escape(chapter.title or ""),
        body=body,
    )


def fix_text(text: str, direction: str = "vertical") -> str:
    """Public helper for tests."""
    return _fix_text(text, direction)


def render_colophon_xhtml(doc: Document) -> str:
    body = (
        '<div class="colophon">\n'
        f"  <p>タイトル: {escape(doc.title)}</p>\n"
        + (f"  <p>著者: {escape(doc.author)}</p>\n" if doc.author else "")
        + f"  <p>原本: {escape(doc.source_pdf)}</p>\n"
        "  <p>OCRエンジン: <a href=\"https://github.com/kotaro-kinoshita/yomitoku\">YomiToku</a> "
        "(CC BY-NC-SA 4.0)</p>\n"
        "  <p>本EPUBは pdf2epub によって生成されました。"
        "OCRエンジンのライセンスにより、本ファイルの商用利用はできません。</p>\n"
        "</div>\n"
    )
    return XHTML_TEMPLATE.format(lang=doc.language, title="奥付", body=body)
