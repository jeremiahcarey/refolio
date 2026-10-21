"""One-click cleanup operations on Refolio Markdown.

Each operation takes the whole document and returns (new_document, count_of_changes).
They never touch the front matter, and only the ones that say so touch footnotes.
"""
from __future__ import annotations

import re
from collections import Counter

from .epub import parse_front_matter
from .textutil import continues, join_hyphenated, vocab

PAGE_MARK = re.compile(r"^<!--\s*page\s+\d+\s*-->$", re.I)
NOTE_DEF = re.compile(r"^\[\^[^\]]+\]:")


def _split(md: str):
    """-> (front matter text, list of blocks). Blocks are separated by blank lines."""
    front = ""
    if md.startswith("---\n"):
        end = md.find("\n---", 4)
        if end != -1:
            front = md[: end + 4] + "\n\n"
            md = md[end + 4:]
    blocks = [b.strip("\n") for b in re.split(r"\n[ \t]*\n", md.strip("\n"))]
    return front, [b for b in blocks if b.strip()]


def _join(front: str, blocks: list[str]) -> str:
    return front + "\n\n".join(blocks).strip() + "\n"


def _is_text_block(b: str) -> bool:
    s = b.lstrip()
    return not (PAGE_MARK.match(s) or NOTE_DEF.match(s) or s.startswith(("#", "<!--", "---")))


# --------------------------------------------------------------------------

def remove_page_numbers(md: str):
    front, blocks = _split(md)
    pat = re.compile(r"^\s*(page\s*)?(\d{1,4}|[ivxlcdm]{1,7})(\s*(of|/)\s*\d+)?\s*$", re.I)
    kept = [b for b in blocks if not pat.match(b)]
    return _join(front, kept), len(blocks) - len(kept)


def remove_repeated_lines(md: str):
    """Short blocks that occur 3+ times (running headers that slipped through)."""
    front, blocks = _split(md)

    def key(b):
        return re.sub(r"\d+", "#", b.strip().lower())
    counts = Counter(key(b) for b in blocks if _is_text_block(b) and len(b) < 150)
    kept = [b for b in blocks if not (_is_text_block(b) and len(b) < 150 and counts[key(b)] >= 3)]
    return _join(front, kept), len(blocks) - len(kept)


def fix_hyphenation(md: str):
    """'philo- sophy' -> 'philosophy' (keeps real compounds like 'self- evident')."""
    front, blocks = _split(md)
    vocab.learn(re.sub(r"\w+-[ \t]+\w+", " ", " ".join(blocks)))
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return join_hyphenated(m.group(1) + "-", m.group(2))
    out = [re.sub(r"(\w+)-[ \t]+([a-z]\w*)", rep, b) if _is_text_block(b) or NOTE_DEF.match(b) else b
           for b in blocks]
    return _join(front, out), n


def join_broken_paragraphs(md: str):
    """Rejoin paragraphs split by a page or column break (the first doesn't end a
    sentence and the next starts in lower case). Page markers in between move
    after the joined paragraph."""
    front, blocks = _split(md)
    out: list[str] = []
    pending_marks: list[str] = []
    n = 0
    last_text = None  # index in out of the last paragraph
    for b in blocks:
        s = b.strip()
        if PAGE_MARK.match(s) or NOTE_DEF.match(s):
            pending_marks.append(b)
            continue
        if (last_text is not None and _is_text_block(s) and not s.startswith(">")
                and not out[last_text].lstrip().startswith(">")
                and continues(out[last_text], s)):
            out[last_text] = join_hyphenated(out[last_text], s)
            n += 1
            continue
        out.extend(pending_marks)
        pending_marks = []
        out.append(b)
        last_text = len(out) - 1 if _is_text_block(s) else None
    out.extend(pending_marks)
    return _join(front, out), n


def remove_unlinked_superscripts(md: str):
    n = len(re.findall(r"<sup>.*?</sup>", md))
    return re.sub(r"<sup>.*?</sup>", "", md), n


CITATION = re.compile(
    r"\s?\((?:(?:see|cf\.|e\.g\.,?)\s+)?"
    r"(?:[A-Z][\w'’\-]+(?:\s(?:and|&)\s[A-Z][\w'’\-]+|\set\sal\.)?,?\s(?:\d{4}[a-z]?|n\.d\.|forthcoming)"
    r"(?:,\s?\d{4}[a-z]?)*(?:[,:]\s?(?:pp?\.\s?)?[\dixvl]+(?:[–\-]\d+)?)?"
    r"(?:;\s?)?)+\)")


def remove_citations(md: str):
    """Remove author-year citations such as '(Smith 1990, 23)' or
    '(see Jones and Lee 2004; Kim 2010a: 4–6)'. Footnotes are included."""
    front, blocks = _split(md)
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return ""
    out = [CITATION.sub(rep, b) if (_is_text_block(b) or NOTE_DEF.match(b)) else b for b in blocks]
    return _join(front, out), n


URL = re.compile(r"\s?(?:<)?(?:https?://|www\.|doi:\s?|https?://doi\.org/)[^\s)>\]]+(?:>)?")


def remove_urls(md: str):
    front, blocks = _split(md)
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return ""
    out = [URL.sub(rep, b) if (_is_text_block(b) or NOTE_DEF.match(b)) else b for b in blocks]
    return _join(front, out), n


# id: (full label, button label, group, function)
OPERATIONS = {
    "join_paragraphs": ("Rejoin split paragraphs", "Split paragraphs", "Fix", join_broken_paragraphs),
    "hyphens": ("Fix broken hyphenation", "Hyphenation", "Fix", fix_hyphenation),
    "page_numbers": ("Remove page numbers", "Page numbers", "Remove", remove_page_numbers),
    "repeated": ("Remove repeated header lines", "Repeated lines", "Remove", remove_repeated_lines),
    "superscripts": ("Remove unlinked superscripts", "Stray superscripts", "Remove", remove_unlinked_superscripts),
    "citations": ("Remove (Author 2001) citations", "Citations", "Remove", remove_citations),
    "urls": ("Remove web addresses and DOIs", "Web addresses", "Remove", remove_urls),
}


def run(op: str, md: str):
    if op not in OPERATIONS:
        raise KeyError(op)
    return OPERATIONS[op][3](md)


def drop_pages(md: str, pages: set[int]):
    """Delete everything between the markers of the given pages (e.g. a cover page)."""
    meta_front, blocks = _split(md)
    out, cur, n = [], None, 0
    for b in blocks:
        m = re.match(r"^<!--\s*page\s+(\d+)\s*-->$", b.strip(), re.I)
        if m:
            cur = int(m.group(1))
        if cur in pages:
            n += 1
            continue
        out.append(b)
    return _join(meta_front, out), n


__all__ = ["OPERATIONS", "run", "drop_pages", "parse_front_matter"]
