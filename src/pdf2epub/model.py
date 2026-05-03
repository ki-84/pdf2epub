from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WritingMode = Literal["vertical", "horizontal"]
PageDir = Literal["rtl", "ltr"]
BlockRole = Literal["heading", "paragraph", "list_item", "caption"]


@dataclass
class RubyRun:
    base: str
    ruby: str


@dataclass
class TextRun:
    text: str
    rubies: list[RubyRun] = field(default_factory=list)


@dataclass
class Block:
    role: BlockRole
    level: int
    runs: list[TextRun]
    direction: WritingMode


@dataclass
class Chapter:
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Document:
    title: str
    language: str = "ja"
    writing_mode: WritingMode = "horizontal"
    page_direction: PageDir = "ltr"
    chapters: list[Chapter] = field(default_factory=list)
    source_pdf: str = ""
    author: str = ""
