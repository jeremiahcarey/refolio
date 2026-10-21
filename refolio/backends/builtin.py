"""Fast converter that needs no machine-learning models.

It reads the PDF's text layer with pdftext and uses layout heuristics:
  * running headers/footers and page numbers are dropped (text that repeats in the
    top or bottom margin across pages, or is just a number),
  * reading order follows columns (recursive XY-cut),
  * footnotes are small-print blocks at the bottom of a column,
  * headings are larger or bold short lines, block quotes are indented on both sides.

It works well on born-digital papers (Word exports, LaTeX, most journal PDFs).
Scanned PDFs have no text layer; use the Marker converter for those.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from ..model import Block, Extracted
from ..textutil import (vocab, escape_block_start, escape_inline, ends_sentence,
                        is_note_mark, join_hyphenated, normalize_spaces)

MARGIN_ZONE = 0.09  # top/bottom fraction of the page treated as header/footer zone


@dataclass
class Run:
    text: str
    size: float
    italic: bool = False
    bold: bool = False
    sup: bool = False


@dataclass
class Line:
    runs: list[Run]
    bbox: tuple

    @property
    def plain(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class TBlock:
    lines: list[Line]
    bbox: tuple
    page: int
    size: float = 0.0
    kind: str = "para"
    level: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def plain(self) -> str:
        return " ".join(l.plain.strip() for l in self.lines)

    @property
    def x0(self): return self.bbox[0]

    @property
    def y0(self): return self.bbox[1]

    @property
    def x1(self): return self.bbox[2]

    @property
    def y1(self): return self.bbox[3]


# ---------------------------------------------------------------------------
# reading the PDF

def _span_style(span) -> tuple[bool, bool]:
    font = span.get("font") or {}
    name = (font.get("name") or "").lower()
    flags = font.get("flags") or 0
    weight = font.get("weight") or 400
    italic = bool(flags & 64) or "italic" in name or "oblique" in name or re.search(r"cmti|cmsl|-it\b|,it", name) is not None
    bold = weight >= 600 or "bold" in name or "black" in name or "semibold" in name or re.search(r"cmbx|cmb\d", name) is not None
    return italic, bold


MARK_CHARS = set("0123456789*†‡§¶,")


def _char_h(c) -> float:
    b = c["bbox"]
    return b[3] - b[1]


def _bogus_size_runs(span, line_med, italic, bold) -> list[Run]:
    """Some producers (Word's PDF export, for one) scale text through the text matrix
    and leave the font size at 1.0, so pdftext can't tell body text from small print or
    superscripts, and glues a note mark onto the word before it. The character boxes
    still carry the real geometry: use their heights as the size, and treat small
    digits and symbols as superscripts."""
    runs: list[Run] = []
    cur: list = []
    cur_sup = False

    def flush():
        if not cur:
            return
        hs = [h for h in (_char_h(c) for c in cur) if h > 0]
        size = statistics.median(hs) if hs else line_med
        runs.append(Run("".join(c["char"] for c in cur).replace("\n", "").replace("\r", ""),
                        size, italic, bold, cur_sup))

    for c in span["chars"]:
        h = _char_h(c)
        small = bool(line_med) and 0 < h < 0.8 * line_med and c["char"] in MARK_CHARS
        if cur and h > 0 and small != cur_sup:
            flush()
            cur = []
        if h > 0:
            cur_sup = small
        cur.append(c)
    flush()
    return [r for r in runs if r.text]


def _line_size(l: Line) -> float:
    sizes = [r.size for r in l.runs if not r.sup and r.text.strip()]
    return max(sizes) if sizes else 0.0


def _split_at_note_marks(l: Line) -> list[Line]:
    """pdftext sometimes fuses two short footnotes into one line ("7 Apuleius, XI, 5.
    8 Tuzuk..."). Start a new line at a note mark that follows a finished sentence."""
    out: list[Line] = []
    cur: list[Run] = []
    for r in l.runs:
        prev = "".join(x.text for x in cur).strip()
        if r.sup and is_note_mark(r.text) and prev and ends_sentence(prev):
            out.append(Line(cur, l.bbox))
            cur = []
        cur.append(r)
    out.append(Line(cur, l.bbox))
    return out


def _split_by_size(tb: TBlock) -> list[TBlock]:
    """pdftext can put the last paragraph of a page and its footnotes in one block.
    Split wherever the print size changes by more than ~8%."""
    if len(tb.lines) < 2:
        return [tb]
    sizes = [_line_size(l) for l in tb.lines]
    # a line that is only a note mark takes the size of the line after it
    for i in range(len(sizes) - 1, -1, -1):
        if not sizes[i]:
            sizes[i] = sizes[i + 1] if i + 1 < len(sizes) else sizes[i - 1] if i else 0.0
    groups: list[list[Line]] = [[tb.lines[0]]]
    for prev, cur, l in zip(sizes, sizes[1:], tb.lines[1:]):
        if prev and cur and abs(prev - cur) > 0.08 * max(prev, cur):
            groups.append([])
        groups[-1].append(l)
    if len(groups) == 1:
        return [tb]
    out = []
    for g in groups:
        bb = (min(l.bbox[0] for l in g), min(l.bbox[1] for l in g),
              max(l.bbox[2] for l in g), max(l.bbox[3] for l in g))
        out.append(TBlock(g, bb, tb.page))
    return out


def _is_centred_line(l: Line, w: float) -> bool:
    x0, _, x1, _ = l.bbox
    return abs((x0 + x1) / 2 - w / 2) < w * 0.02 and (x1 - x0) < w * 0.6


def _split_centred(tb: TBlock, w: float) -> list[TBlock]:
    """Titles centred above body text often share a pdftext block with the text (or
    with a byline). Split runs of centred short lines from the rest."""
    flags = [_is_centred_line(l, w) for l in tb.lines]
    if all(flags):
        # a centred title and an italic byline under it are different things
        flags = [all(r.italic for r in l.runs if r.text.strip()) for l in tb.lines]
    if len(tb.lines) < 2 or all(flags) or not any(flags):
        return [tb]
    groups: list[list[Line]] = [[tb.lines[0]]]
    for prev, cur, l in zip(flags, flags[1:], tb.lines[1:]):
        if prev != cur:
            groups.append([])
        groups[-1].append(l)
    out = []
    for g in groups:
        bb = (min(l.bbox[0] for l in g), min(l.bbox[1] for l in g),
              max(l.bbox[2] for l in g), max(l.bbox[3] for l in g))
        out.append(TBlock(g, bb, tb.page))
    return out


def _read_pages(pdf_path):
    from pdftext.extraction import dictionary_output

    pages = dictionary_output(str(pdf_path), keep_chars=True, disable_links=True)
    out = []
    for pi, page in enumerate(pages):
        blocks = []
        for b in page["blocks"]:
            lines = []
            for l in b["lines"]:
                runs = []
                sizes = [s["font"].get("size") or 0 for s in l["spans"] if s["text"].strip()]
                main = max(sizes) if sizes else 0
                heights = [h for s in l["spans"] for c in s.get("chars") or ()
                           if (h := _char_h(c)) > 0]
                line_med = statistics.median(heights) if heights else 0
                for s in l["spans"]:
                    text = s["text"].replace("\n", "")
                    if not text:
                        continue
                    size = s["font"].get("size") or main
                    italic, bold = _span_style(s)
                    if size < 3 and s.get("chars"):
                        runs.extend(_bogus_size_runs(s, line_med, italic, bold))
                        continue
                    sup = bool(s.get("superscript")) or (
                        main and size < 0.8 * main and is_note_mark(text))
                    runs.append(Run(text, size, italic, bold, sup))
                if runs and "".join(r.text for r in runs).strip():
                    lines.append(Line(runs, tuple(l["bbox"])))
            if lines:
                tb = TBlock(lines, tuple(b["bbox"]), pi + 1)
                # small note marks relative to the block's text size are superscripts,
                # even when the mark sits alone on its own line
                bsize = _block_size(tb)
                for ln in lines:
                    for r in ln.runs:
                        if not r.sup and bsize and r.size < 0.8 * bsize and is_note_mark(r.text):
                            r.sup = True
                for part in _split_by_size(tb):
                    blocks.extend(_split_centred(part, page["width"]))
        out.append((page["width"], page["height"], blocks))
    return out


def _block_size(b: TBlock) -> float:
    c = Counter()
    for l in b.lines:
        for r in l.runs:
            if not r.sup:
                c[round(r.size, 1)] += len(r.text.strip())
    return c.most_common(1)[0][0] if c else 0.0


# ---------------------------------------------------------------------------
# reading order: recursive XY-cut

def _groups(items, lo, hi, min_gap):
    items = sorted(items, key=lambda b: lo(b))
    groups, cur, end = [], [], None
    for b in items:
        if cur and lo(b) > end + min_gap:
            groups.append(cur)
            cur, end = [], None
        cur.append(b)
        end = hi(b) if end is None else max(end, hi(b))
    if cur:
        groups.append(cur)
    return groups


def xy_cut(items, depth=0):
    """Order blocks for reading: columns left to right, spanning rows top to bottom.

    Horizontal cuts are only made next to rows that contain a wide (spanning) block,
    so that gaps which happen to line up across two columns don't interleave them."""
    if len(items) <= 1 or depth > 40:
        return sorted(items, key=lambda b: (b.y0, b.x0))
    cols = _groups(items, lambda b: b.x0, lambda b: b.x1, 6)
    if len(cols) > 1:
        return [x for c in cols for x in xy_cut(c, depth + 1)]
    rows = _groups(items, lambda b: b.y0, lambda b: b.y1, 0.5)
    if len(rows) > 1:
        width = max(1.0, max(b.x1 for b in items) - min(b.x0 for b in items))
        # column structure comes from paragraph-like (multi-line) narrow blocks;
        # titles and headings are too short to tell us where the gutter is
        narrow = [b for b in items if b.x1 - b.x0 < 0.6 * width and len(b.lines) >= 2]
        ncols = _groups(narrow, lambda b: b.x0, lambda b: b.x1, 6) if narrow else []
        if len(ncols) < 2:
            return [x for r in rows for x in xy_cut(r, depth + 1)]
        gutters = [(max(b.x1 for b in a), min(b.x0 for b in c)) for a, c in zip(ncols, ncols[1:])]

        def spanning(row):
            return any(b.x0 < g1 and b.x1 > g0 for b in row for g0, g1 in gutters)

        merged = [rows[0]]
        for r in rows[1:]:
            if not spanning(r) and not spanning(merged[-1]):
                merged[-1] = merged[-1] + r
            else:
                merged.append(r)
        if len(merged) > 1:
            return [x for r in merged for x in xy_cut(r, depth + 1)]
    return sorted(items, key=lambda b: (b.y0, b.x0))


# ---------------------------------------------------------------------------
# inline text

def _runs_to_md(runs: list[Run], allow_italic=True) -> str:
    out = []
    # merge adjacent runs with identical style
    merged: list[Run] = []
    for r in runs:
        if merged and (merged[-1].italic, merged[-1].bold, merged[-1].sup) == (r.italic, r.bold, r.sup):
            merged[-1] = Run(merged[-1].text + r.text, r.size, r.italic, r.bold, r.sup)
        else:
            merged.append(Run(r.text, r.size, r.italic, r.bold, r.sup))
    for r in merged:
        t = escape_inline(r.text)
        if r.sup:
            core = t.strip()
            if is_note_mark(core):
                out.append(f"<sup>{core}</sup>")
            else:
                out.append(t)
            continue
        if allow_italic and (r.italic or r.bold) and t.strip():
            lead = t[: len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()):]
            mark = "**" if r.bold and not r.italic else "*"
            out.append(f"{lead}{mark}{t.strip()}{mark}{trail}")
        else:
            out.append(t)
    return "".join(out)


def _lines_to_text(lines: list[Line], allow_italic=True) -> str:
    text = ""
    for l in lines:
        piece = _runs_to_md(l.runs, allow_italic)
        text = join_hyphenated(text, piece) if text else piece.strip()
    text = re.sub(r"\*\s*\*", "", text)  # "*a* *b*" artefacts collapse to "*a b*"
    return normalize_spaces(text)


def _split_paragraphs(b: TBlock) -> list[list[Line]]:
    """pdftext sometimes puts several paragraphs in one block. Split where a line is
    indented relative to the block's left edge after a line that ended a sentence
    short of the right edge."""
    if len(b.lines) < 3:
        return [b.lines]
    left = statistics.median(l.bbox[0] for l in b.lines)
    right = max(l.bbox[2] for l in b.lines)
    paras, cur = [], [b.lines[0]]
    for prev, l in zip(b.lines, b.lines[1:]):
        indented = l.bbox[0] - left > 6
        prev_short = right - prev.bbox[2] > 15
        if (indented or prev_short) and ends_sentence(prev.plain) and prev_short:
            paras.append(cur)
            cur = []
        cur.append(l)
    paras.append(cur)
    return paras


# ---------------------------------------------------------------------------
# main entry point

def _starts_with_mark(b: TBlock) -> bool:
    first = next((r for r in b.lines[0].runs if r.text.strip()), None)
    return first is not None and first.sup and is_note_mark(first.text)


def _find_footnotes(live, bodyish, body, h):
    """Footnotes are the stack of blocks at the bottom of each column that are in
    smaller print than the body text or begin with a note mark."""
    found = set()
    for b in live:
        small = b.size <= body * 0.93
        marked = _starts_with_mark(b)
        # long notes can start well up the page; a note mark alone is weaker evidence
        if b.y0 < h * (0.15 if small else 0.35):
            continue
        if not (small or marked):
            continue
        same_col = [o for o in live if o is not b
                    and min(o.x1, b.x1) - max(o.x0, b.x0) > 0.3 * min(b.x1 - b.x0, o.x1 - o.x0)]
        below = [o for o in same_col if o.y0 > b.y0 + 1]
        if all(o.size <= body * 0.93 or _starts_with_mark(o) for o in below):
            found.add(id(b))
    return found


def _vocab_text(pages) -> str:
    """All words of the document except the fragments around line-break hyphens."""
    parts = []
    for _, _, blocks in pages:
        for b in blocks:
            prev_hyph = False
            for l in b.lines:
                words = l.plain.split()
                hyph = bool(words) and words[-1].endswith(("-", "\u00ad"))
                if prev_hyph and words:
                    words = words[1:]
                if hyph and words:
                    words = words[:-1]
                parts.append(" ".join(words))
                prev_hyph = hyph
    return " ".join(parts)


def _norm_margin_text(t: str) -> str:
    t = re.sub(r"\d+", "#", t.lower())
    return re.sub(r"\s+", " ", t).strip()


PAGE_NUM_RE = re.compile(r"^\s*(page\s*)?(\d{1,4}|[ivxlcdm]{1,7})(\s*(of|/)\s*\d+)?\s*$", re.I)
BOILERPLATE_RE = re.compile(
    r"(downloaded from|all use subject to|this content downloaded|jstor\.org/terms|"
    r"for personal use only|copyright ©|©\s*\d{4}|doi\.org/10\.)", re.I)


def extract(pdf_path, progress=None) -> Extracted:
    pages = _read_pages(pdf_path)
    n = len(pages)
    warnings: list[str] = []
    if progress:
        progress(0.3, "Reading layout")
    vocab.learn(_vocab_text(pages))

    # body font size: the size carrying the most characters
    sizes = Counter()
    for _, _, blocks in pages:
        for b in blocks:
            b.size = _block_size(b)
            sizes[b.size] += sum(len(l.plain) for l in b.lines)
    body = sizes.most_common(1)[0][0] if sizes else 10.0

    # JSTOR-style cover page
    skip_pages = set()
    if n > 1:
        first = " ".join(b.plain for b in pages[0][2]).lower()
        if "jstor" in first and ("terms" in first or "stable url" in first):
            skip_pages.add(1)
            warnings.append("Skipped page 1 (looks like a JSTOR cover page).")

    # running headers / footers
    margin_counts = Counter()
    for w, h, blocks in pages:
        seen = set()
        for b in blocks:
            if len(b.lines) <= 2 and (b.y1 < h * MARGIN_ZONE or b.y0 > h * (1 - MARGIN_ZONE)):
                seen.add(_norm_margin_text(b.plain))
        margin_counts.update(seen)
    # alternating recto/verso headers appear on only about half the pages
    repeat_min = max(2, 0.4 * n) if n < 6 else max(3, 0.25 * n)
    removed_margin = 0
    for w, h, blocks in pages:
        for b in blocks:
            if b.page in skip_pages:
                b.kind = "skip"
                continue
            in_margin = b.y1 < h * MARGIN_ZONE or b.y0 > h * (1 - MARGIN_ZONE)
            if not in_margin or len(b.lines) > 2:
                continue
            t = b.plain
            if (PAGE_NUM_RE.match(t) or BOILERPLATE_RE.search(t)
                    or (n >= 2 and re.search(r"\w", t) and margin_counts[_norm_margin_text(t)] >= repeat_min)):
                b.kind = "skip"
                removed_margin += 1

    if progress:
        progress(0.6, "Finding footnotes and headings")

    heading_sizes = sorted({b.size for _, _, bl in pages for b in bl
                            if b.kind != "skip" and b.size >= body * 1.15 and len(b.lines) <= 3},
                           reverse=True)
    title = ""
    author = ""
    title_block = None
    out: list[Block] = []
    for w, h, blocks in pages:
        live = [b for b in blocks if b.kind != "skip"]
        bodyish = [b for b in live if abs(b.size - body) <= body * 0.06]
        notes_here = _find_footnotes(live, bodyish, body, h)
        for b in live:
            if id(b) in notes_here:
                b.kind = "footnote"
                continue
            words = len(b.plain.split())
            if b.size >= body * 1.15 and len(b.lines) <= 3 and words <= 30:
                b.kind = "heading"
                b.level = 1 + heading_sizes.index(b.size)
                continue
            all_bold = all(r.bold for l in b.lines for r in l.runs if r.text.strip() and not r.sup)
            if all_bold and len(b.lines) <= 2 and words <= 15 and not ends_sentence(b.plain):
                b.kind = "heading"
                b.level = len(heading_sizes) + 1
                continue
            # a short centred line with no closing punctuation is a section title even
            # when it is set in body type (common in Word-made PDFs)
            if (len(b.lines) <= 2 and words <= 12 and b.size <= body * 1.15
                    and all(_is_centred_line(l, w) for l in b.lines)
                    and not ends_sentence(b.plain) and not re.match(r"^[\d\W]", b.plain)
                    and not re.search(r"(?i)\b(the|of|and|a|an|to|in|that|is|are|also|or)$", b.plain)
                    and not all(r.italic for l in b.lines for r in l.runs if r.text.strip())):
                b.kind = "heading"
                b.level = len(heading_sizes) + 2
                continue
            # block quote: indented on both sides relative to its column
            col = [o for o in bodyish if o is not b
                   and min(o.x1, b.x1) - max(o.x0, b.x0) > 0.5 * (b.x1 - b.x0)]
            if col and len(b.lines) >= 2:
                cx0 = min(o.x0 for o in col)
                cx1 = max(o.x1 for o in col)
                if b.x0 - cx0 > 10 and cx1 - b.x1 > 10 and b.size <= body * 1.02:
                    b.kind = "quote"

        ordered = xy_cut(live)
        notes = [b for b in ordered if b.kind == "footnote"]
        for b in ordered:
            if b.kind == "footnote":
                continue
            if b.kind == "heading":
                text = _lines_to_text(b.lines, allow_italic=False).replace("**", "")
                plain = re.sub(r"<sup>.*?</sup>", "", text).strip()
                if not title and b.level == 1 and b.page <= 2:
                    title = plain
                    title_block = b
                    out.append(Block("heading", text, b.page, b.level, b.bbox))
                    continue
                # a centred short line right under the title is a byline, not a heading
                centred = abs((b.x0 + b.x1) / 2 - w / 2) < w * 0.08
                if (title and out and out[-1].kind == "heading" and out[-1].level == 1
                        and b.page == title_block.page and centred
                        and len(plain.split()) <= 10 and not re.match(r"^\d", plain)):
                    if not author:
                        author = plain
                    out.append(Block("para", text, b.page, 0, b.bbox))
                    continue
                out.append(Block("heading", text, b.page, b.level, b.bbox))
                continue
            plain = b.plain.strip()
            if (title and not author and out and out[-1].kind == "heading" and out[-1].level == 1
                    and title_block is not None and b.page == title_block.page and len(b.lines) <= 2
                    and abs((b.x0 + b.x1) / 2 - w / 2) < w * 0.08 and len(plain.split()) <= 8
                    and not ends_sentence(plain.rstrip("."))):
                author = re.sub(r"<sup>.*?</sup>", "", _lines_to_text(b.lines, False)).strip()
            for para in _split_paragraphs(b):
                text = escape_block_start(_lines_to_text(para))
                if text:
                    out.append(Block(b.kind, text, b.page, 0, b.bbox))
        for b in notes:
            # a block may hold several notes: start a new one at each leading mark
            cur: list[Line] = []
            for l in (x for ln in b.lines for x in _split_at_note_marks(ln)):
                first = next((r for r in l.runs if r.text.strip()), None)
                starts = first is not None and (
                    (first.sup and is_note_mark(first.text))
                    or (re.match(r"^\s*\d{1,3}[.)]?\s+\S", l.plain) and l is not b.lines[0]
                        and l.bbox[0] <= b.x0 + 12 and cur))
                if starts and cur:
                    out.append(Block("footnote", _lines_to_text(cur), b.page, 0, b.bbox))
                    cur = []
                cur.append(l)
            if cur:
                out.append(Block("footnote", _lines_to_text(cur), b.page, 0, b.bbox))

    # heading levels: the document title is level 1, section levels start at 2
    levels = sorted({b.level for b in out if b.kind == "heading"})
    rank = {lv: i for i, lv in enumerate(levels)}
    for b in out:
        if b.kind != "heading":
            continue
        if title and b.text and re.sub(r"<sup>.*?</sup>", "", b.text).strip() == title and b.page <= 2:
            b.level = 1
        else:
            b.level = min(6, rank[b.level] + (1 if title else 2))
            b.level = max(b.level, 2)

    if removed_margin:
        warnings.append(f"Removed {removed_margin} running header/footer/page-number lines.")
    return Extracted(out, n, title=title, author=author, warnings=warnings)
