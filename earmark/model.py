"""The small document model every converter produces.

A converter turns a PDF into a flat list of Blocks in reading order. Inline text is
Markdown, except that footnote reference marks are left as ``<sup>…</sup>`` so the
assembler can match them to the footnotes it finds.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str          # heading | para | quote | list | caption | footnote
    text: str          # inline Markdown
    page: int          # 1-based page number in the source PDF
    level: int = 0     # heading level (1 = document title)
    bbox: tuple | None = None


@dataclass
class Extracted:
    blocks: list[Block]
    page_count: int
    title: str = ""
    author: str = ""
    warnings: list[str] = field(default_factory=list)
