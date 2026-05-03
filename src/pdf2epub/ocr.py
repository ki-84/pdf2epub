from __future__ import annotations

import os
from typing import Any, Iterator


def detect_device(requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def iter_pdf_images(pdf_path: str, dpi: int = 200) -> Iterator[Any]:
    """Lazily render PDF pages to BGR numpy arrays (matches YomiToku format).

    Unlike `yomitoku.data.functions.load_pdf` this releases each page's bitmap
    after yielding, keeping memory usage to roughly one page at a time.
    """
    import numpy as np
    import pypdfium2

    doc = pypdfium2.PdfDocument(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            try:
                bitmap = page.render(scale=dpi / 72)
                pil_image = bitmap.to_pil().convert("RGB")
                img = np.array(pil_image)[:, :, ::-1]  # RGB -> BGR
                yield img
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        doc.close()


def count_pdf_pages(pdf_path: str) -> int:
    import pypdfium2

    doc = pypdfium2.PdfDocument(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def analyze_pdf(
    pdf_path: str,
    *,
    device: str = "auto",
    ignore_ruby: bool = False,
    ruby_threshold: float = 0.5,
    start_page: int = 1,
    max_pages: int | None = None,
    progress: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield one normalized page-dict per PDF page.

    Each yielded dict is the JSON representation of YomiToku's
    DocumentAnalyzerSchema for that page.
    """
    resolved_device = detect_device(device)
    if resolved_device == "mps":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    from yomitoku import DocumentAnalyzer

    analyzer = DocumentAnalyzer(
        visualize=False,
        device=resolved_device,
        ignore_ruby=ignore_ruby,
        ruby_threshold=ruby_threshold,
    )

    total = count_pdf_pages(pdf_path)
    start_idx = max(0, start_page - 1)
    end_idx = total if max_pages is None else min(total, start_idx + max_pages)

    import sys
    import time

    image_iter = iter_pdf_images(pdf_path)
    # Skip pages before start_idx without holding them in memory.
    for _ in range(start_idx):
        try:
            next(image_iter)
        except StopIteration:
            return

    for offset in range(end_idx - start_idx):
        page_no = start_idx + offset + 1
        try:
            img = next(image_iter)
        except StopIteration:
            break
        if progress:
            print(f"[ocr] page {page_no}/{total} ...", file=sys.stderr, flush=True)
            t0 = time.perf_counter()
        results, _ocr_vis, _layout_vis = analyzer(img)
        # Release the rendered bitmap before yielding so RAM stays flat.
        del img
        page_dict = _to_dict(results)
        if progress:
            dt = time.perf_counter() - t0
            print(f"[ocr] page {page_no} done in {dt:.1f}s", file=sys.stderr, flush=True)
        yield page_dict


def _to_dict(results: Any) -> dict[str, Any]:
    """Best-effort conversion of YomiToku result object to a plain dict."""
    if hasattr(results, "model_dump"):
        return results.model_dump()
    if hasattr(results, "dict"):
        return results.dict()
    if isinstance(results, dict):
        return results
    raise TypeError(f"Cannot convert YomiToku result to dict: {type(results)!r}")
