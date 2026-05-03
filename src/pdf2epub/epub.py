from __future__ import annotations

import os
import re
import uuid
import zipfile
from pathlib import Path

from ebooklib import epub

from pdf2epub.model import Document
from pdf2epub.render import render_chapter_xhtml, render_colophon_xhtml

_STYLES_DIR = Path(__file__).resolve().parent / "styles"


def _load_css(writing_mode: str) -> str:
    name = "vertical.css" if writing_mode == "vertical" else "horizontal.css"
    return (_STYLES_DIR / name).read_text(encoding="utf-8")


def build_epub(doc: Document, output_path: str) -> None:
    book = epub.EpubBook()

    book.set_identifier(f"urn:uuid:{uuid.uuid4()}")
    book.set_title(doc.title)
    book.set_language(doc.language)
    if doc.author:
        book.add_author(doc.author)

    css_item = epub.EpubItem(
        uid="style_main",
        file_name="style.css",
        media_type="text/css",
        content=_load_css(doc.writing_mode),
    )
    book.add_item(css_item)

    chapter_items: list[epub.EpubHtml] = []
    for i, chapter in enumerate(doc.chapters, start=1):
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{i:03d}.xhtml",
            lang=doc.language,
        )
        item.content = render_chapter_xhtml(chapter, language=doc.language)
        item.add_item(css_item)
        book.add_item(item)
        chapter_items.append(item)

    colophon = epub.EpubHtml(
        title="奥付",
        file_name="colophon.xhtml",
        lang=doc.language,
    )
    colophon.content = render_colophon_xhtml(doc)
    colophon.add_item(css_item)
    book.add_item(colophon)

    book.toc = tuple(chapter_items) + (colophon,)
    book.add_item(epub.EpubNcx())

    book.add_item(epub.EpubNav())

    book.spine = ["nav", *chapter_items, colophon]

    # Try to set direction via ebooklib API if present (best-effort).
    try:
        book.set_direction(doc.page_direction)  # type: ignore[attr-defined]
    except Exception:
        try:
            book.direction = doc.page_direction  # type: ignore[attr-defined]
        except Exception:
            pass

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    # epub3_pages defaults to True and crashes on items without <body>
    # children; we don't emit pagebreak markers, so disable it.
    epub.write_epub(output_path, book, {"epub3_pages": False})

    _patch_opf_direction(output_path, doc.page_direction)


def _patch_opf_direction(epub_path: str, direction: str) -> None:
    """Guarantee `<spine page-progression-direction="...">` in the OPF.

    ebooklib's API for spine direction is inconsistent across versions, so we
    rewrite the OPF file inside the ZIP to make sure the attribute is present.
    """
    tmp_path = epub_path + ".tmp"
    with zipfile.ZipFile(epub_path, "r") as zin, zipfile.ZipFile(tmp_path, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.lower().endswith(".opf"):
                data = _patch_spine_bytes(data, direction)
            zout.writestr(info, data, compress_type=info.compress_type)
    os.replace(tmp_path, epub_path)


_SPINE_OPEN_RE = re.compile(rb"<spine\b([^>]*)>", re.IGNORECASE)
_PPD_ATTR_RE = re.compile(rb'page-progression-direction\s*=\s*"[^"]*"', re.IGNORECASE)


def _patch_spine_bytes(data: bytes, direction: str) -> bytes:
    dir_bytes = direction.encode("ascii")
    if _PPD_ATTR_RE.search(data):
        return _PPD_ATTR_RE.sub(b'page-progression-direction="' + dir_bytes + b'"', data)

    def _inject(m: "re.Match[bytes]") -> bytes:
        attrs = m.group(1)
        return b"<spine" + attrs + b' page-progression-direction="' + dir_bytes + b'">'

    return _SPINE_OPEN_RE.sub(_inject, data, count=1)
