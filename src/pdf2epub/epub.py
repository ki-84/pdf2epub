from __future__ import annotations

import os
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from ebooklib import epub

from pdf2epub.model import Block, Document
from pdf2epub.render import render_chapter_xhtml, render_colophon_xhtml

_STYLES_DIR = Path(__file__).resolve().parent / "styles"

# Figures larger than this dimension (in source pixels) get downscaled to keep
# EPUB size manageable. 200dpi A5 page is ~1183x1690, so 1200 keeps full-page
# diagrams legible without bloating the file.
FIGURE_MAX_DIM = 1200


def _load_css(writing_mode: str) -> str:
    name = "vertical.css" if writing_mode == "vertical" else "horizontal.css"
    return (_STYLES_DIR / name).read_text(encoding="utf-8")


def _figure_blocks(doc: Document) -> list[Block]:
    out: list[Block] = []
    for ch in doc.chapters:
        for b in ch.blocks:
            if b.role == "figure" and b.image_source_page is not None and b.image_bbox:
                out.append(b)
    return out


def _emit_figure_images(book: epub.EpubBook, doc: Document) -> None:
    """Render figure crops from the source PDF and add them as EPUB images."""
    figures = _figure_blocks(doc)
    if not figures or not doc.source_pdf:
        return
    if not Path(doc.source_pdf).exists():
        return

    try:
        import pypdfium2
        from PIL import Image  # noqa: F401
    except Exception:
        return

    by_page: dict[int, list[Block]] = {}
    for i, b in enumerate(figures, start=1):
        b.image_href = f"images/figure_{i:04d}.png"
        # Stash a sanitized uid (used for OPF item id; must be a valid
        # XML NCName — no '/' or ':' allowed).
        setattr(b, "_image_uid", f"img_{i:04d}")
        by_page.setdefault(b.image_source_page or 0, []).append(b)

    pdf_doc = pypdfium2.PdfDocument(doc.source_pdf)
    try:
        for page_idx in sorted(by_page.keys()):
            try:
                page = pdf_doc[page_idx]
            except Exception:
                continue
            try:
                bitmap = page.render(scale=200 / 72)
                pil_img = bitmap.to_pil().convert("RGB")
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            for b in by_page[page_idx]:
                assert b.image_bbox is not None
                x1, y1, x2, y2 = b.image_bbox
                # Clamp to image bounds.
                w, h = pil_img.size
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                crop = pil_img.crop((x1, y1, x2, y2))
                # Downscale very large figures.
                cw, ch = crop.size
                m = max(cw, ch)
                if m > FIGURE_MAX_DIM:
                    s = FIGURE_MAX_DIM / m
                    crop = crop.resize((int(cw * s), int(ch * s)))
                buf = BytesIO()
                crop.save(buf, format="PNG", optimize=True)
                book.add_item(
                    epub.EpubImage(
                        uid=getattr(b, "_image_uid", f"img_{id(b):x}"),
                        file_name=b.image_href,
                        media_type="image/png",
                        content=buf.getvalue(),
                    )
                )
    finally:
        pdf_doc.close()


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

    # Emit figure images first so chapters can reference them via image_href.
    _emit_figure_images(book, doc)

    chapter_items: list[epub.EpubHtml] = []
    for i, chapter in enumerate(doc.chapters, start=1):
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{i:03d}.xhtml",
            lang=doc.language,
        )
        item.content = render_chapter_xhtml(
            chapter, language=doc.language, doc_writing_mode=doc.writing_mode
        )
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
