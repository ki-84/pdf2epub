from __future__ import annotations

from xml.sax.saxutils import escape

from pdf2epub.model import Block, Chapter, Document, RubyRun, TextRun


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


def _render_run_with_rubies(run: TextRun) -> str:
    if not run.rubies:
        return escape(run.text)
    text = run.text
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


def _render_block(block: Block) -> str:
    inner = "".join(_render_run_with_rubies(r) for r in block.runs)
    if block.role == "heading":
        level = max(1, min(block.level or 1, 6))
        return f"<h{level}>{inner}</h{level}>\n"
    if block.role == "list_item":
        return f"<ul><li>{inner}</li></ul>\n"
    if block.role == "caption":
        return f'<p class="no-indent"><em>{inner}</em></p>\n'
    return f"<p>{inner}</p>\n"


def render_chapter_xhtml(chapter: Chapter, *, language: str) -> str:
    body = "".join(_render_block(b) for b in chapter.blocks)
    return XHTML_TEMPLATE.format(
        lang=escape(language, {'"': "&quot;"}),
        title=escape(chapter.title or ""),
        body=body,
    )


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
