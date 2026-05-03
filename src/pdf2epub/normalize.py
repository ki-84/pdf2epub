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
    r"([一-鿿㐀-䶿豈-﫿々〆〇]+)"
    r"[（(\[]([ぁ-ゟ゠-ヿー]+)[)）\]]"
)

# A ruby paragraph is short and made of kana only.
KANA_ONLY = re.compile(r"^[ぁ-ゟ゠-ヿ・ー\s]+$")
MAX_RUBY_CHARS = 8

# Adjacency tolerance (px @ 200dpi) — Yomitoku ruby boxes typically straddle
# the parent's outer edge.
RUBY_OUTSIDE_TOL = 22       # ruby may protrude up to N px beyond parent edge
RUBY_INSIDE_TOL = 14        # ruby may bite into parent up to N px


def _is_kanji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
        or ch in "々〆〇"
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


def _extract_inline_rubies(text: str) -> tuple[str, list[RubyRun]]:
    """Extract inline 漢字（かんじ） annotations into (cleaned_text, rubies)."""
    rubies: list[RubyRun] = []

    def _sub(m: re.Match[str]) -> str:
        rubies.append(RubyRun(base=m.group(1), ruby=m.group(2)))
        return m.group(1)

    return RUBY_PATTERN.sub(_sub, text), rubies


# ---------------------------------------------------------------------------
# Word-bbox-based ruby attachment
# ---------------------------------------------------------------------------


def _word_box(w: dict[str, Any]) -> tuple[int, int, int, int]:
    pts = w.get("points") or []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if not xs or not ys:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


def _is_ruby_text(text: str) -> bool:
    return bool(text) and len(text) <= MAX_RUBY_CHARS and bool(KANA_ONLY.match(text))


def _box_inside(inner: tuple[int, int, int, int], outer: list[int] | tuple[int, ...]) -> bool:
    if len(outer) != 4:
        return False
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    pad = 4
    return ix1 >= ox1 - pad and iy1 >= oy1 - pad and ix2 <= ox2 + pad and iy2 <= oy2 + pad


def _nearest_kanji_index(text: str, idx: int) -> int | None:
    if not text:
        return None
    n = len(text)
    idx = max(0, min(idx, n - 1))
    for d in range(n):
        for sign in ((0,) if d == 0 else (-1, 1)):
            j = idx + sign * d
            if 0 <= j < n and _is_kanji(text[j]):
                return j
    return None


def _word_score(
    parent_box: tuple[int, int, int, int],
    ruby_box: tuple[int, int, int, int],
    mode: WritingMode,
) -> float | None:
    """Distance score for ruby_word being a ruby of parent_word, or None."""
    px1, py1, px2, py2 = parent_box
    rx1, ry1, rx2, ry2 = ruby_box
    if mode == "vertical":
        # Ruby sits to the right of the parent column.
        if ry1 < py1 - 6 or ry2 > py2 + 6:
            return None
        gap = rx1 - px2
        if -RUBY_INSIDE_TOL <= gap <= RUBY_OUTSIDE_TOL:
            return abs(gap)
        return None
    else:
        # Ruby sits above the parent line.
        if rx1 < px1 - 6 or rx2 > px2 + 6:
            return None
        gap = py1 - ry2
        if -RUBY_INSIDE_TOL <= gap <= RUBY_OUTSIDE_TOL:
            return abs(gap)
        return None


def _attach_rubies_for_page(
    paragraphs: list[dict[str, Any]],
    words: list[dict[str, Any]],
    mode: WritingMode,
) -> tuple[
    list[dict[str, Any]],
    dict[int, list[tuple[str, str]]],
    dict[int, list[str]],
]:
    """Pair ruby words with body words and report ruby annotations per parent paragraph.

    Returns:
        text_paragraphs: paragraphs minus ruby-only ones
        rubies_per_parent: paragraph_id -> [(base, ruby), ...]
        ruby_strings_per_parent: paragraph_id -> [ruby_text, ...] for stripping
            them out of the parent's contents string.
    """
    ruby_word_ids: set[int] = set()
    ruby_words: list[tuple[tuple[int, int, int, int], str]] = []
    body_words: list[tuple[tuple[int, int, int, int], str, dict[str, Any] | None]] = []

    # Index paragraphs for containment lookup (only body-role paragraphs).
    body_paras: list[dict[str, Any]] = []
    ruby_only_para_ids: set[int] = set()
    for p in paragraphs:
        text = (p.get("contents") or "").strip()
        if _is_ruby_text(text) and _classify_role(p.get("role")) == "paragraph":
            ruby_only_para_ids.add(id(p))
            continue
        body_paras.append(p)

    for w in words:
        wbox = _word_box(w)
        wtext = (w.get("content") or "").strip()
        if not wtext:
            continue
        if _is_ruby_text(wtext):
            ruby_words.append((wbox, wtext))
            ruby_word_ids.add(id(w))
            continue
        # Find owning body paragraph (bbox containment).
        owner: dict[str, Any] | None = None
        for p in body_paras:
            if _box_inside(wbox, p.get("box") or []):
                owner = p
                break
        body_words.append((wbox, wtext, owner))

    rubies_per_parent: dict[int, list[tuple[str, str]]] = {}
    ruby_strings_per_parent: dict[int, list[str]] = {}
    for rbox, rtext in ruby_words:
        best: tuple[float, tuple[int, int, int, int], str, dict[str, Any] | None] | None = None
        for pbox, ptext, owner in body_words:
            score = _word_score(pbox, rbox, mode)
            if score is None:
                continue
            if best is None or score < best[0]:
                best = (score, pbox, ptext, owner)
        if best is None or best[3] is None:
            continue
        _, pbox, ptext, owner = best
        # Estimate character index inside the parent word from ruby center.
        if mode == "vertical":
            axis_min, axis_max = pbox[1], pbox[3]
            cand_center = (rbox[1] + rbox[3]) / 2
        else:
            axis_min, axis_max = pbox[0], pbox[2]
            cand_center = (rbox[0] + rbox[2]) / 2
        rel = (cand_center - axis_min) / max(1, axis_max - axis_min)
        rel = max(0.0, min(1.0, rel))
        char_idx = int(rel * len(ptext))
        kanji_idx = _nearest_kanji_index(ptext, char_idx)
        if kanji_idx is None:
            continue
        # Expand around kanji_idx alternately left/right, bounded by
        # roughly ⌈ruby_len/2⌉ kanji (Japanese readings average ~2 kana per
        # kanji, so this keeps compounds intact without absorbing neighbors).
        max_base_len = max(1, (len(rtext) + 1) // 2)
        base_start = kanji_idx
        base_end = kanji_idx + 1
        while (base_end - base_start) < max_base_len:
            extended = False
            if (
                base_start > 0
                and _is_kanji(ptext[base_start - 1])
                and (base_end - base_start) < max_base_len
            ):
                base_start -= 1
                extended = True
            if (
                base_end < len(ptext)
                and _is_kanji(ptext[base_end])
                and (base_end - base_start) < max_base_len
            ):
                base_end += 1
                extended = True
            if not extended:
                break
        base = ptext[base_start:base_end]
        rubies_per_parent.setdefault(id(owner), []).append((base, rtext))
        ruby_strings_per_parent.setdefault(id(owner), []).append(rtext)

    # Drop the ruby-only paragraphs from the returned list.
    text_paras = [p for p in paragraphs if id(p) not in ruby_only_para_ids]
    return text_paras, rubies_per_parent, ruby_strings_per_parent


def _strip_ruby_strings(text: str, ruby_texts: list[str]) -> str:
    """Remove ruby fragments that have leaked into the parent's contents.

    Yomitoku merges ruby and body words into the same paragraph contents
    string. Each ruby reading is removed once (not globally) so we don't
    accidentally erase identical kana sequences elsewhere in the body.
    """
    if not ruby_texts:
        return text
    cleaned = text
    for rt in ruby_texts:
        if not rt:
            continue
        for variant, replacement in (
            (f"\n{rt}\n", "\n"),
            (f"\n{rt}", ""),
            (f"{rt}\n", ""),
            (rt, ""),
        ):
            if variant in cleaned:
                cleaned = cleaned.replace(variant, replacement, 1)
                break
    # Normalize collapsed whitespace.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _para_to_block(
    p: dict[str, Any],
    default_dir: WritingMode,
    *,
    keep_ruby: bool,
    bbox_rubies: list[tuple[str, str]] | None = None,
    ruby_strings: list[str] | None = None,
) -> Block | None:
    role = _classify_role(p.get("role"))
    if role is None:
        return None
    text = (p.get("contents") or "").strip()
    if not text:
        return None

    if ruby_strings:
        text = _strip_ruby_strings(text, ruby_strings)

    if keep_ruby:
        cleaned, rubies = _extract_inline_rubies(text)
    else:
        cleaned, rubies = RUBY_PATTERN.sub(lambda m: m.group(1), text), []

    if bbox_rubies:
        existing_keys = {(r.base, r.ruby) for r in rubies}
        for base, ruby_text in bbox_rubies:
            if (base, ruby_text) in existing_keys:
                continue
            rubies.append(RubyRun(base=base, ruby=ruby_text))
            existing_keys.add((base, ruby_text))

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


def _figure_to_block(
    fig: dict[str, Any],
    page_index: int,
    default_dir: WritingMode,
) -> Block | None:
    bbox = fig.get("box")
    if not bbox or len(bbox) != 4:
        return None
    # Collect text inside the figure as a caption (joined with " / ").
    caption_parts: list[str] = []
    for fp in fig.get("paragraphs") or []:
        if not isinstance(fp, dict):
            continue
        text = (fp.get("contents") or "").strip()
        if text:
            caption_parts.append(text)
    caption = " / ".join(caption_parts)
    return Block(
        role="figure",
        level=0,
        runs=[TextRun(text=caption)],
        direction=default_dir,
        image_source_page=page_index,
        image_bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
    )


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
    keep_figures: bool = True,
    page_index_offset: int = 0,
) -> Document:
    mode = writing_mode or detect_writing_mode(pages)
    direction = page_direction or page_dir_for(mode)

    enum_pages = list(enumerate(pages))
    if reverse_pages:
        enum_pages.reverse()

    blocks: list[Block] = []
    for orig_idx, page in enum_pages:
        page_index = page_index_offset + orig_idx
        page_paras = _sorted_paragraphs(page)
        if keep_ruby:
            words = page.get("words") or []
            text_paras, rubies_per_parent, ruby_strings_per_parent = (
                _attach_rubies_for_page(page_paras, words, mode)
            )
        else:
            text_paras = page_paras
            rubies_per_parent = {}
            ruby_strings_per_parent = {}

        # Build (order, kind, payload) entries, then sort by order so figures
        # interleave with paragraphs in the same reading order Yomitoku found.
        entries: list[tuple[int, str, Any]] = []
        for p in text_paras:
            order = p.get("order") if isinstance(p.get("order"), int) else 10**6
            entries.append((order, "para", p))
        if keep_figures:
            for fig in page.get("figures") or []:
                if not isinstance(fig, dict):
                    continue
                order = fig.get("order") if isinstance(fig.get("order"), int) else 10**6
                entries.append((order, "figure", fig))
        entries.sort(key=lambda e: e[0])

        for _ord, kind, payload in entries:
            if kind == "para":
                block = _para_to_block(
                    payload,
                    default_dir=mode,
                    keep_ruby=keep_ruby,
                    bbox_rubies=rubies_per_parent.get(id(payload)),
                    ruby_strings=ruby_strings_per_parent.get(id(payload)),
                )
            else:
                block = _figure_to_block(payload, page_index, mode)
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


MIN_CHAPTER_BODY_BLOCKS = 2  # ヘディングを除いた本文ブロックの最少数


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


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
            title_text = (b.runs[0].text if b.runs else "").replace("\n", " ").strip()
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

    return _merge_thin_chapters(chapters)


def _merge_thin_chapters(chapters: list[Chapter]) -> list[Chapter]:
    """Fold spurious heading-only / repeated-title chapters into the previous one.

    A chapter survives as its own entry only if it has at least
    `MIN_CHAPTER_BODY_BLOCKS` non-heading blocks AND its title hasn't already
    been used in the immediately preceding chapter. This collapses the long
    tail of cover/back-matter "headings" (book series ad lines, repeated book
    titles, page-header echoes) into a single bucket.
    """
    if not chapters:
        return chapters

    merged: list[Chapter] = []
    seen_titles: set[str] = set()
    for ch in chapters:
        norm = _normalize_title(ch.title)
        body_blocks = sum(1 for b in ch.blocks if b.role != "heading")
        is_thin = body_blocks < MIN_CHAPTER_BODY_BLOCKS
        is_dup = norm in seen_titles
        if merged and (is_thin or is_dup):
            # Fold this chapter into the preceding one and keep going.
            merged[-1].blocks.extend(ch.blocks)
        else:
            merged.append(ch)
            seen_titles.add(norm)
    return merged
