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
from .textutil import SUP_RE, continues, is_note_mark, join_hyphenated

LEAD_MARK_RE = re.compile(r"^\s*(?:<sup>\s*([^<]+?)\s*</sup>|(\d{1,3})[.)]?\s+(?=\S)|([*†‡§¶]+)\s*)")


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


def _split_marks(s: str) -> list[str]:
    s = re.sub(r"<[^>]+>", "", s)
    return [p for p in re.split(r"[,\s]+", s.strip()) if p]


def _collect_notes(blocks: list[Block]) -> list[Note]:
    notes: list[Note] = []
    for b in blocks:
        if b.kind != "footnote":
            continue
        m = LEAD_MARK_RE.match(b.text)
        mark = None
        if m:
            mark = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if not is_note_mark(mark):
                mark = None
        if mark is None:
            if notes:  # continuation of the previous note (column or page break)
                notes[-1].text = join_hyphenated(notes[-1].text, b.text)
                continue
            notes.append(Note(None, b.text.strip(), b.page))
            continue
        notes.append(Note(mark, b.text[m.end():].strip(), b.page))
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
