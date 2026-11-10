"""Clause-aware chunking: split a contract at its own structure (numbered sections, articles, headings), then pack
the pieces into chunks of a target size. Every chunk keeps exact character offsets into the source text, so a
citation can always be traced back and checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 1200
MAX_CHARS = 2000

# A new clause starts at a line like "12.", "12.3", "Section 4", "ARTICLE IX", "(a)" or an all-caps heading.
BOUNDARY = re.compile(
    r"(?m)^[ \t]*(?:"
    r"(?:ARTICLE|Article|SECTION|Section)\s+[0-9IVXLC]+[.:]?"
    r"|\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Z(]"
    r"|\([a-z]{1,3}\)\s"
    r"|[A-Z][A-Z \-&,]{6,60}$"
    r")"
)


@dataclass(frozen=True)
class Chunk:
    idx: int
    start: int
    end: int
    heading: str
    text: str


def _heading(piece: str) -> str:
    first = piece.strip().split("\n", 1)[0].strip()
    return first[:120] if len(first) <= 120 else ""


def _split_long(start: int, end: int, text: str) -> list[tuple[int, int]]:
    """Split a span longer than MAX_CHARS at sentence or whitespace boundaries."""
    spans, s = [], start
    while end - s > MAX_CHARS:
        window = text[s:s + MAX_CHARS]
        cut = max(window.rfind(". "), window.rfind("\n"))
        if cut < TARGET_CHARS // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_CHARS
        spans.append((s, s + cut + 1))
        s = s + cut + 1
    spans.append((s, end))
    return spans


def chunk_text(text: str) -> list[Chunk]:
    starts = sorted({0, *(m.start() for m in BOUNDARY.finditer(text))})
    pieces = [(a, b) for a, b in zip(starts, starts[1:] + [len(text)]) if text[a:b].strip()]
    spans: list[tuple[int, int]] = []
    cur_start, cur_end = None, None
    for a, b in pieces:
        if cur_start is None:
            cur_start, cur_end = a, b
        elif b - cur_start <= TARGET_CHARS:
            cur_end = b
        else:
            spans.extend(_split_long(cur_start, cur_end, text))
            cur_start, cur_end = a, b
    if cur_start is not None:
        spans.extend(_split_long(cur_start, cur_end, text))
    chunks = []
    for i, (a, b) in enumerate(spans):
        while a < b and text[a].isspace():
            a += 1
        chunks.append(Chunk(i, a, b, _heading(text[a:b]), text[a:b]))
    return chunks
