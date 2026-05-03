from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

from pdf2epub import __version__
from pdf2epub.epub import build_epub
from pdf2epub.model import Document
from pdf2epub.normalize import build_document
from pdf2epub.ocr import analyze_pdf, detect_device
from pdf2epub.render import render_chapter_xhtml, render_colophon_xhtml


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf2epub",
        description=(
            "スキャン画像PDFをYomiToku OCRで読み取りEPUB 3を生成します。"
            "縦書き/横書き、右開き/左開き、ルビに対応。"
            "OCRエンジンYomiTokuのライセンス(CC BY-NC-SA 4.0)に従い、"
            "本ツールおよび生成EPUBの商用利用はできません。"
        ),
    )
    p.add_argument("input", help="入力PDFファイル")
    p.add_argument("-o", "--output", required=True, help="出力EPUBパス")
    p.add_argument(
        "--device",
        choices=["auto", "mps", "cuda", "cpu"],
        default="auto",
        help="推論デバイス (default: auto)",
    )
    p.add_argument(
        "--writing-mode",
        choices=["auto", "vertical", "horizontal"],
        default="auto",
        help="書字方向 (default: auto = OCR結果から多数決)",
    )
    p.add_argument(
        "--page-direction",
        choices=["auto", "rtl", "ltr"],
        default="auto",
        help="ページ進行方向 (default: auto = writing-modeから決定)",
    )
    p.add_argument("--no-ruby", action="store_true", help="ルビを抽出しない")
    p.add_argument(
        "--no-figures",
        action="store_true",
        help="挿絵（図表）をEPUBに埋め込まない（テキストのみ）",
    )
    p.add_argument(
        "--image-format",
        choices=["jpeg", "png"],
        default="jpeg",
        help=(
            "挿絵の画像形式 (default: jpeg, baseline)。"
            "JPEG はXTEINK X4 / Cross Point 等の e-ink リーダーで確実に表示できる。"
            "PNG にすると透過は保てるが古いリーダーで表示されないことがある"
        ),
    )
    p.add_argument(
        "--image-max-dim",
        type=int,
        default=1000,
        help="挿絵の長辺ピクセル上限 (default: 1000)。e-ink向けは 800 程度を推奨",
    )
    p.add_argument(
        "--image-grayscale",
        action="store_true",
        help="挿絵をグレースケールで保存 (e-inkリーダーでファイルサイズ削減)",
    )
    p.add_argument(
        "--reverse-pages",
        action="store_true",
        help="PDFが物理本の最終ページから先頭へ逆順にスキャンされている場合に指定",
    )
    p.add_argument(
        "--ruby-threshold",
        type=float,
        default=0.5,
        help="ルビ判定の閾値 (default: 0.5)",
    )
    p.add_argument("--title", default=None, help="EPUBタイトル (default: PDFファイル名)")
    p.add_argument("--author", default="", help="著者名")
    p.add_argument(
        "--debug-html",
        default=None,
        help="中間XHTMLを書き出すディレクトリ",
    )
    p.add_argument(
        "--dump-json",
        default=None,
        help="正規化済みDocumentをJSONで保存するパス",
    )
    p.add_argument(
        "--dump-raw-json",
        default=None,
        help="YomiTokuの生JSONをページ単位で書き出すディレクトリ",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="先頭Nページのみ処理（動作確認用）",
    )
    p.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="開始ページ番号（1始まり、default: 1）",
    )
    p.add_argument(
        "--rebuild-from",
        default=None,
        help=(
            "OCRをスキップし、--dump-raw-jsonで保存済みのディレクトリから"
            "EPUBを再生成する（input.pdf は挿絵切抜き用に必要）"
        ),
    )
    p.add_argument("--version", action="version", version=f"pdf2epub {__version__}")
    return p


def _resolve_writing_mode(arg: str) -> str | None:
    return None if arg == "auto" else arg


def _resolve_page_direction(arg: str) -> str | None:
    return None if arg == "auto" else arg


def _dump_raw_pages(pages: list[dict[str, Any]], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for i, page in enumerate(pages, start=1):
        path = Path(out_dir) / f"page_{i:04d}.json"
        path.write_text(
            json.dumps(page, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _dump_document(doc: Document, path: str) -> None:
    Path(path).write_text(
        json.dumps(dataclasses.asdict(doc), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _dump_debug_html(doc: Document, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for i, ch in enumerate(doc.chapters, start=1):
        path = Path(out_dir) / f"chap_{i:03d}.xhtml"
        path.write_text(render_chapter_xhtml(ch, language=doc.language), encoding="utf-8")
    (Path(out_dir) / "colophon.xhtml").write_text(
        render_colophon_xhtml(doc), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not os.path.exists(args.input):
        print(f"[pdf2epub] 入力PDFが見つかりません: {args.input}", file=sys.stderr)
        return 2

    if args.rebuild_from:
        if not os.path.isdir(args.rebuild_from):
            print(
                f"[pdf2epub] --rebuild-from に指定されたディレクトリがありません: {args.rebuild_from}",
                file=sys.stderr,
            )
            return 2
        json_files = sorted(
            f for f in os.listdir(args.rebuild_from) if f.endswith(".json")
        )
        pages = []
        for fn in json_files:
            with open(os.path.join(args.rebuild_from, fn), encoding="utf-8") as fh:
                pages.append(json.load(fh))
        print(
            f"[pdf2epub] 生JSON {len(pages)} ページを {args.rebuild_from} から読込",
            file=sys.stderr,
        )
    else:
        device = detect_device(args.device)
        print(f"[pdf2epub] device = {device}", file=sys.stderr)
        print(f"[pdf2epub] OCR 実行中: {args.input}", file=sys.stderr)

        pages = list(
            analyze_pdf(
                args.input,
                device=device,
                ignore_ruby=args.no_ruby,
                ruby_threshold=args.ruby_threshold,
                start_page=args.start_page,
                max_pages=args.max_pages,
                progress=True,
            )
        )
        print(f"[pdf2epub] OCR 完了: {len(pages)} ページ", file=sys.stderr)

    if args.dump_raw_json:
        _dump_raw_pages(pages, args.dump_raw_json)
        print(f"[pdf2epub] 生JSON書き出し: {args.dump_raw_json}", file=sys.stderr)

    doc = build_document(
        pages,
        pdf_path=args.input,
        title=args.title,
        author=args.author,
        writing_mode=_resolve_writing_mode(args.writing_mode),  # type: ignore[arg-type]
        page_direction=_resolve_page_direction(args.page_direction),  # type: ignore[arg-type]
        keep_ruby=not args.no_ruby,
        reverse_pages=args.reverse_pages,
        keep_figures=not args.no_figures,
        page_index_offset=max(0, args.start_page - 1),
    )
    print(
        f"[pdf2epub] writing_mode={doc.writing_mode} page_direction={doc.page_direction} "
        f"chapters={len(doc.chapters)}",
        file=sys.stderr,
    )

    if args.dump_json:
        _dump_document(doc, args.dump_json)
        print(f"[pdf2epub] Document JSON: {args.dump_json}", file=sys.stderr)

    if args.debug_html:
        _dump_debug_html(doc, args.debug_html)
        print(f"[pdf2epub] 中間XHTML: {args.debug_html}", file=sys.stderr)

    build_epub(
        doc,
        args.output,
        image_format=args.image_format,
        image_max_dim=args.image_max_dim,
        image_grayscale=args.image_grayscale,
    )
    print(f"[pdf2epub] EPUB 出力: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
