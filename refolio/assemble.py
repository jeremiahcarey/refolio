"""Turn converter Blocks into the editable Markdown document.

The Markdown uses:
  * ``<!-- page N -->`` markers, so the editor can show the matching PDF page,
  * Pandoc-style footnotes: ``[^3]`` in the text and ``[^3]: note text`` definitions,
    placed at the end of the page they came from,
  * a small front-matter block with title and author.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Block, Extracted
from .textutil import (OCR_MARK_CHARS, SUP_RE, continues, is_note_mark, join_hyphenated, ocr_digits,
                       ocr_number_pattern)

LEAD_MARK_RE = re.compile(r"^\s*(?:<sup>\s*([^<]+?)\s*</sup>|(\d{1,3})[.)]?\s+(?=\S)|([†‡§¶]+|\*+(?=\s))\s*)")


@dataclass
class Note:
    mark: str | None
    text: str
    page: int
    label: str | None = None
    used: bool = False


@dataclass
class Assembled:
    markdown: str
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


EMPTY_SUP_RE = re.compile(r"\s*<sup>\s*</sup>\s*")


def _split_marks(s: str) -> list[str]:
    s = re.sub(r"<[^>]+>", "", s)
    return [p for p in re.split(r"[,\s]+", s.strip()) if p]


# a second note's mark fused onto the end of the previous note's block
FUSED_NOTE_RE = re.compile(r"(?<=[.)”\"'’])\s+(?=<sup>\s*\d{1,3}\s*</sup>)")


# "<sup>47.</sup>": some converters keep the period of "47." inside the superscript
SUP_PERIOD_RE = re.compile(r"<sup>\s*(\d{1,3})[.)]\s*</sup>")
# an OCR-garbled lead such as "i. Richard Sorabji" (1) or "io. Stephen Walker" (10)
OCR_LEAD_RE = re.compile(rf"^\s*([{re.escape(OCR_MARK_CHARS)}]{{1,3}})[.)]\s+(?=\S)")


def _split_numbered(text: str, n: int) -> list[tuple[int, str]]:
    """Split a run of notes fused into one block wherever the next number in
    sequence (n + 1, possibly OCR-garbled) starts a new sentence."""
    parts = []
    while True:
        nxt = ocr_number_pattern(n + 1)
        rx = re.compile(rf"(?<=[.)”\"'’\]])\s+(?:<sup>\s*)?{nxt}[.)]?(?:\s*</sup>)?"
                        r"\s+(?=\*{0,2}[A-Z“\"'‘(\[]|https?:)"
                        # an explicit superscript needs no sentence end before it ("... .html <sup>30</sup>")
                        rf"|\s*<sup>\s*{n + 1}[.)]?\s*</sup>\s*")
        m = rx.search(text)
        if not m:
            parts.append((n, text))
            return parts
        parts.append((n, text[:m.start()]))
        text = text[m.end():]
        n += 1


def _collect_notes(blocks: list[Block]) -> list[Note]:
    notes: list[Note] = []
    pieces = [Block(b.kind, t, b.page, b.level, b.bbox)
              for b in blocks if b.kind == "footnote"
              for t in FUSED_NOTE_RE.split(SUP_PERIOD_RE.sub(r"<sup>\1</sup>", b.text))]
    for b in pieces:
        # Marker leaves an empty <sup> where the number was lost: it is the next one in sequence
        if EMPTY_SUP_RE.match(b.text) and notes and (notes[-1].mark or "").isdigit():
            b.text = f"<sup>{int(notes[-1].mark) + 1}</sup>" + EMPTY_SUP_RE.sub("", b.text, count=1)
        else:
            b.text = EMPTY_SUP_RE.sub("", b.text)
        m = LEAD_MARK_RE.match(b.text)
        mark = None
        if m:
            mark = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if not is_note_mark(mark):
                mark = None
        if mark is None:
            m = OCR_LEAD_RE.match(b.text)
            val = ocr_digits(m.group(1)) if m else None
            prev = int(notes[-1].mark) if notes and (notes[-1].mark or "").isdigit() else None
            if val is not None and (prev is None or val in (1, prev + 1)):
                mark = str(val)
        if mark is None:
            if notes:  # continuation of the previous note (column or page break)
                prev = notes[-1]
                joined = join_hyphenated(prev.text, b.text)
                # the next note's number may be buried in it ("... claims. 3 See, e.g., ...")
                parts = _split_numbered(joined, int(prev.mark)) if (prev.mark or "").isdigit() else [(0, joined)]
                prev.text = parts[0][1].strip()
                notes.extend(Note(str(k), t.strip(), b.page) for k, t in parts[1:])
                continue
            notes.append(Note(None, b.text.strip(), b.page))
            continue
        rest = b.text[m.end():].strip()
        if mark.isdigit():
            for n, t in _split_numbered(rest, int(mark)):
                notes.append(Note(str(n), t.strip(), b.page))
        else:
            notes.append(Note(mark, rest, b.page))
    return notes


def _find_note(notes: list[Note], mark: str, page: int) -> Note | None:
    cands = [n for n in notes if n.mark == mark and not n.used]
    if not cands:
        return None
    # prefer the same page, then the next pages (notes can spill over), then anywhere
    for n in cands:
        if n.page == page:
            return n
    later = [n for n in cands if n.page > page]
    if later:
        return min(later, key=lambda n: n.page)
    return cands[0]


def _plain_mark_re(mark: str) -> re.Pattern:
    m = re.escape(mark)
    return re.compile(
        # "civilization.”1 One", "wake”3—for": digits glued to the word or punctuation before
        rf"(?<=[A-Za-z.,;:!?”’\"')\]])(?<!\d){m}(?!\d)(?=[\s—–]|$)"
        # Marker spaces the mark out: "status. 3 Th is", but not "ch. 3 and" or "p. 5 ."
        rf"|(?<=[.!?”’\"')\]])(?<!\bp\.)(?<!\bpp\.)(?<!\bch\.)(?<!\bchs\.)(?<!\bno\.)(?<!\bvol\.)"
        rf"(?<!\bsec\.)(?<!\bfig\.)[ ]{m}(?!\d)(?=\s*(?:$|[;:,.)]|[A-Z“\"‘(]))"
        # "ethics, 9 feminist", "qualifications 8 )", "respectively 4 ;"
        rf"|(?<=[,;:])[ ]{m}(?!\d)(?=\s)"
        rf"|(?<=[A-Za-z])[ ]{m}(?=\s*[;:,)—–])")


def _promote_plain_marks(notes: list[Note], body: list[Block]) -> int:
    """Some converters lose the superscript formatting, leaving a note mark as plain
    digits stuck to the end of a sentence. For each numbered note with no <sup>
    reference nearby, turn the first such plain number on its page into one."""
    promoted = 0
    cursor = 0
    for n in notes:
        if not n.mark or not n.mark.isdigit():
            continue
        pages = (n.page, n.page - 1)
        sup = re.compile(rf"<sup>\s*{n.mark}\s*</sup>")
        if any(b.page in pages and sup.search(b.text) for b in body if b.kind != "heading"):
            continue
        rx = _plain_mark_re(n.mark)
        for page in pages:
            hit = None
            for i in range(cursor if page == n.page else 0, len(body)):
                b = body[i]
                if b.page != page or b.kind == "heading":
                    continue
                m = rx.search(b.text)
                if m:
                    hit = (i, m)
                    break
            if hit:
                i, m = hit
                tail = re.sub(r"^\s+(?=[;:,.])", "", body[i].text[m.end():])  # "respectively 4 ;"
                body[i].text = body[i].text[:m.start()] + f"<sup>{n.mark}</sup>" + tail
                cursor = i
                promoted += 1
                break
    return promoted


def assemble(ex: Extracted) -> Assembled:
    warnings = list(ex.warnings)
    notes = _collect_notes(ex.blocks)
    body = [b for b in ex.blocks if b.kind != "footnote"]

    # merge paragraphs split by column / page breaks
    merged: list[Block] = []
    for b in body:
        prev = merged[-1] if merged else None
        if (prev and prev.kind in ("para", "quote") and b.kind == prev.kind
                and continues(prev.text, b.text)):
            prev.text = join_hyphenated(prev.text, b.text)
            continue
        merged.append(Block(b.kind, b.text, b.page, b.level, b.bbox))

    promoted = _promote_plain_marks(notes, merged)
    if promoted:
        warnings.append(
            f"{promoted} note mark(s) had lost their superscript formatting in the PDF text and "
            "were matched to their notes by number and position; worth a quick check.")

    # link reference marks to notes
    counter = 0
    unmatched = []

    def link(match, page):
        nonlocal counter
        marks = _split_marks(match.group(1))
        if not marks or not all(is_note_mark(m) for m in marks):
            return match.group(0)
        out = []
        for m in marks:
            n = _find_note(notes, m, page)
            if n is None:
                unmatched.append((m, page))
                out.append(f"<sup>{m}</sup>")
                continue
            n.used = True
            counter += 1
            n.label = str(counter)
            out.append(f"[^{n.label}]")
        return "".join(out)

    for b in merged:
        if b.kind == "heading":
            # marks on headings (e.g. an acknowledgement note on the title)
            b.text = SUP_RE.sub(lambda m: link(m, b.page), b.text)
        else:
            b.text = SUP_RE.sub(lambda m: link(m, b.page), b.text)

    orphans = [n for n in notes if not n.used]
    for n in orphans:
        counter += 1
        n.label = str(counter)
    if unmatched:
        warnings.append(
            f"{len(unmatched)} reference mark(s) had no matching footnote and were left as "
            "superscripts (search for <sup>).")
    if orphans:
        warnings.append(
            f"{len(orphans)} footnote(s) had no reference mark in the text; they will be "
            "listed at the end of the notes.")

    # write markdown
    lines: list[str] = []
    meta_title = ex.title.replace('"', "'")
    meta_author = ex.author.replace('"', "'")
    lines += ["---", f'title: "{meta_title}"', f'author: "{meta_author}"', "---", ""]
    notes_by_page: dict[int, list[Note]] = {}
    for n in notes:
        notes_by_page.setdefault(n.page, []).append(n)

    cur_page = None

    def flush_notes(page):
        for n in notes_by_page.pop(page, []):
            text = n.text.replace("\n", " ")
            lines.append(f"[^{n.label}]: {text}")
            lines.append("")

    pages_seen = []
    for b in merged:
        if b.page != cur_page:
            if cur_page is not None:
                flush_notes(cur_page)
            # also flush notes of pages that had no body blocks
            for p in sorted(k for k in notes_by_page if cur_page is not None and cur_page < k < b.page):
                lines.append(f"<!-- page {p} -->")
                lines.append("")
                flush_notes(p)
            cur_page = b.page
            pages_seen.append(b.page)
            lines.append(f"<!-- page {b.page} -->")
            lines.append("")
        if b.kind == "heading":
            lines.append("#" * max(1, min(b.level, 6)) + " " + b.text)
        elif b.kind == "quote":
            lines.append("> " + b.text)
        elif b.kind == "list":
            lines.append("- " + b.text)
        elif b.kind == "caption":
            lines.append("*" + b.text.strip("*") + "*")
        else:
            lines.append(b.text)
        lines.append("")
    if cur_page is not None:
        flush_notes(cur_page)
    for p in sorted(notes_by_page):
        lines.append(f"<!-- page {p} -->")
        lines.append("")
        flush_notes(p)

    md = "\n".join(lines).rstrip() + "\n"
    stats = {"pages": ex.page_count, "notes": len(notes), "linked": len(notes) - len(orphans),
             "unmatched_refs": len(unmatched)}
    return Assembled(md, warnings, stats)
