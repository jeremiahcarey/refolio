"""Text helpers shared by the converters and the cleanup tools."""
from __future__ import annotations

import itertools
import re

# Prefixes that usually keep their hyphen when a line breaks after them
# ("self-\nevident" -> "self-evident", but "philo-\nsophy" -> "philosophy").
KEEP_HYPHEN_PREFIXES = {
    "self", "non", "anti", "quasi", "pre", "post", "co", "ex", "neo", "pseudo",
    "semi", "sub", "super", "ultra", "well", "ill", "meta", "proto", "cross",
    "counter", "inter", "intra", "multi", "half", "all", "first", "second",
    "third", "higher", "lower", "long", "short", "so", "un",
}

TERMINAL = tuple('.?!:;"”’)]…')
SUP_RE = re.compile(r"<sup>(.*?)</sup>", re.S)
NOTE_MARK_RE = re.compile(r"^\s*(\d{1,3}|[*†‡§¶‖#]+)\s*$")


# Some PDFs (OUP, Cambridge and others) set note marks as Unicode superscript digits
# ("peers.⁴", "¹⁰ Beeley (2008)") instead of using a raised, smaller font.
SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_TO_ASCII = str.maketrans(SUPERSCRIPT_DIGITS, "0123456789")
SUP_DIGITS_RE = re.compile(f"[{SUPERSCRIPT_DIGITS}]+")


def unicode_sup_to_digits(s: str) -> str:
    return s.translate(_SUP_TO_ASCII)


def supify(text: str) -> str:
    """Turn runs of Unicode superscript digits into <sup>12</sup> reference marks."""
    return SUP_DIGITS_RE.sub(lambda m: f"<sup>{unicode_sup_to_digits(m.group(0))}</sup>", text)


# OCR often reads note numbers as look-alike letters: "i." for 1, "io." for 10
# (capitals are left out: "S. Harrison" or "B. Russell" are initials, not notes 5 and 8)
_OCR_DIGITS = {"0": "0o", "1": "1il|!", "2": "2z", "5": "5s", "6": "6b", "8": "8", "9": "9g"}
_OCR_TO_DIGIT = {c: d for d, cs in _OCR_DIGITS.items() for c in cs}
OCR_MARK_CHARS = "".join(_OCR_TO_DIGIT) + "34567"


def ocr_number_pattern(n: int) -> str:
    """Regex source matching n written in digits or in OCR look-alikes."""
    return "".join(f"[{re.escape(_OCR_DIGITS.get(d, d))}]" for d in str(n))


def ocr_digits(s: str) -> int | None:
    """The number a possibly OCR-garbled mark stands for, or None."""
    if not s or not all(c in OCR_MARK_CHARS for c in s):
        return None
    d = "".join(_OCR_TO_DIGIT.get(c, c) for c in s)
    return int(d) if d.isdigit() else None


def is_note_mark(s: str) -> bool:
    s = re.sub(r"<[^>]+>", "", s)
    return bool(NOTE_MARK_RE.match(s.strip()))


class _Vocab:
    """Words seen in the current document, used to decide whether a hyphen at a line
    break belongs to the word ("eighteenth-century") or only to the line break
    ("philo-sophy")."""

    def __init__(self):
        self.words: set[str] = set()

    def learn(self, text: str):
        self.words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*[A-Za-z]", text)}

    def keep_hyphen(self, a: str, b: str) -> bool | None:
        a, b = a.lower(), b.lower()
        if f"{a}-{b}" in self.words and f"{a}{b}" not in self.words:
            return True
        if f"{a}{b}" in self.words:
            return False
        if a in KEEP_HYPHEN_PREFIXES:
            return True
        if a in self.words and b in self.words and len(a) > 3 and len(b) > 3:
            return True
        return None


vocab = _Vocab()


def join_hyphenated(left: str, right: str) -> str:
    """Join two pieces of text that were split at a line break."""
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    # a word hyphenated inside italics: "*inde-*" + "*fensible*" -> "*indefensible*"
    close, opened = re.search(r"[-\u00ad¬](\*{1,2})$", left), re.match(r"\*{1,2}", right)
    if close and opened and close.group(1) == opened.group(0):
        left, right = left[:-len(close.group(1))], right[opened.end():]
    m = re.search(r"(\w+)[-\u00ad¬]$", left)
    if m and right[:1].islower():
        nxt = re.match(r"\w+", right)
        if vocab.keep_hyphen(m.group(1), nxt.group(0) if nxt else ""):
            return left[:-1] + "-" + right
        return left[:-1] + right
    if left.endswith(("\u00ad", "¬")):
        return left[:-1] + right
    return left + " " + right


def ends_sentence(text: str) -> bool:
    t = re.sub(r"(<sup>.*?</sup>|\[\^[^\]]+\])+$", "", text.rstrip()).rstrip()
    t = t.rstrip("*_")  # closing emphasis
    return t.endswith(TERMINAL)


def starts_lowercase(text: str) -> bool:
    t = text.lstrip("*_ \"“‘(")
    return t[:1].islower()


def continues(prev: str, nxt: str) -> bool:
    """Does `nxt` look like the second half of a paragraph `prev` that was split
    by a column or page break?"""
    if prev.rstrip().endswith("-"):
        return True
    return not ends_sentence(prev) and (starts_lowercase(nxt) or prev.rstrip().endswith(","))


def escape_block_start(text: str) -> str:
    """Stop a paragraph's first characters from being read as Markdown syntax."""
    if re.match(r"^(#{1,6}\s|>|[-+*]\s|\d+[.)]\s)", text):
        return "\\" + text
    return text


def escape_inline(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")


# Marker joins the lines of a block with two spaces; html_to_inline can keep that as this
# character so the mender can tell "ex  tends" (a line break) from "ex tends".
LINE_BREAK = "\ue000"
# the second word is only looked at, not consumed, so "qualifi ed" is found in "is qualifi ed."
_SPLIT_WORD_RE = re.compile(
    rf"(?<![A-Za-z-])(?<![A-Za-z]['’])([A-Za-z]{{2,}})([ \t{LINE_BREAK}]+)(?=([A-Za-z]+)(?![A-Za-z-])(?!['’][A-Za-z]))")
_GLUED_RE = re.compile(r"(?<![A-Za-z-])[A-Za-z]{6,}(?![A-Za-z-])")
_LIGATURE_END = ("Th", "fi", "fl", "ff", "ffi", "ffl")
_COMMON = set("""in to on no so as at he it is us an or be by do go if my of up we me the and for are but not you
all can any one was has had may per too out off our its who why how his her him she they them than then that this
these those with from into onto some more most many much such what when were been have will would could should""".split())
_SUFFIXES = ("ing", "ion", "ers", "ed", "es", "er", "ly", "s", "d")


def _stem(w: str) -> str:
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)]
    return w


def word_set(text: str) -> set[str]:
    """Lower-case words of `text`, plus its hyphenated compounds ("single-file") whole."""
    words = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", text)}
    words |= {w.lower() for w in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)+", text)}
    return words


_LIG_CODES = '!"#$%&'
_LIG_CLASS = re.escape(_LIG_CODES)
# an opening quote may sit right before the ligature: `"#e metaphysical` is "The metaphysical
_LIG_TOKEN_RE = re.compile(rf'(?<![A-Za-z0-9])(?P<q>")?(?P<tok>[A-Za-z]*(?:[{_LIG_CLASS}][A-Za-z]*)+)(?![A-Za-z0-9])')
# a word broken after a hyphen at a line end, the next line opening with the ligature: "justi- !ed"
_LIG_BREAK_RE = re.compile(rf"(?<![A-Za-z])([A-Za-z]+)-[ \t{LINE_BREAK}]+([{_LIG_CLASS}][A-Za-z]*)(?![A-Za-z0-9])")
_LIG_GHOST_RE = re.compile(rf"(?<![A-Za-z])(?P<head>[A-Za-z]*)[{_LIG_CLASS}][ \t{LINE_BREAK}]+(?P<tail>[a-z]+)(?![A-Za-z])")
_LIG_START = ("Th", "fi", "ff", "fl", "ffi", "ffl")
_LIG_INNER = ("ffi", "ffl", "fi", "fl", "ff", "Th")


def repair_ligature_codes(text: str, plain: set[str]) -> str:
    """A text extractor that can't read a PDF's ligature font returns the glyph's character
    code, one that changes from page to page: "justi!ed", "o$er", "#e" for justified, offer, The.
    Put a ligature back where that gives a word in the lexicon and the code alone does not."""
    def expand(tok: str, first_is_start: bool) -> str | None:
        pieces = re.split(f"[{_LIG_CLASS}]", tok)
        k = len(pieces) - 1
        if k < 1 or k > 2 or not any(pieces):
            return None
        for combo in itertools.product(*[(_LIG_START if (i == 0 and first_is_start and not pieces[0]) else _LIG_INNER)
                                         for i in range(k)]):
            cand = pieces[0] + "".join(c + pieces[i + 1] for i, c in enumerate(combo))
            if len(cand) > 2 and cand.lower() in plain:
                return cand
        return None

    def fix(m: re.Match) -> str:
        tok = m.group("tok")
        if tok.lower() in plain:
            return m.group(0)
        cand = expand(tok, True)
        return (m.group("q") or "") + cand if cand else m.group(0)

    def join(m: re.Match) -> str:
        head, tail = m.groups()
        pieces = re.split(f"[{_LIG_CLASS}]", tail)
        for c in _LIG_INNER:
            if (head + c + pieces[-1]).lower() in plain:
                return head + c + pieces[-1]
        return m.group(0)

    def ghost(m: re.Match) -> str:
        head, tail = m.group("head"), m.group("tail")
        for c in (_LIG_START if not head else _LIG_INNER):
            if (head + c + tail).lower() in plain:
                return head + c + tail
        return m.group(0)

    # a ligature that is an italic run of its own: "*!**e Routledge*" -> "*!e Routledge*"
    text = re.sub(rf"\*([{_LIG_CLASS}])\*\*(?=[A-Za-z])", r"*\1", text)
    text = _LIG_BREAK_RE.sub(join, text)
    # the extractor's space after the ligature: "justi! cation"
    text = _LIG_GHOST_RE.sub(ghost, text)
    return _LIG_TOKEN_RE.sub(fix, text)


def mend_split_words(text: str, words: set[str]) -> str:
    """Repair words that a converter split in two, or glued where it should not have:
    a ligature's ghost space ("Th is", "qualifi ed"), a line-break hyphen it dropped
    ("ex tends") or one it swallowed ("equalconsideration"). `words` is a lexicon of
    correctly spelled words, taken from another reading of the same PDF.

    Two neighbours are joined only when the joined form is in it and the pair looks split: a
    half is not a word, the first ends in a ligature ("Th", "ff"), or the break is a line
    break and neither half is a common small word. So "off the" and "any one" stay apart."""
    plain = {w for w in words if "-" not in w}
    text = repair_ligature_codes(text, plain)
    glued = {w.replace("-", ""): w for w in words if "-" in w}
    stems = {_stem(w) for w in plain}

    def fix(m: re.Match) -> str:
        a, sep, b = m.groups()
        la, lb = a.lower(), b.lower()
        if len(b) < 2 and not a.endswith(_LIGATURE_END):
            return m.group(0)
        joined = la + lb
        # the lexicon may only have another form of the word ("offers" for "off er")
        if joined not in plain and joined not in glued and _stem(joined) not in stems:
            return m.group(0)
        # two real words at a line break stay apart even if the lexicon has them as a hyphenated
        # compound: the break may or may not have hidden the hyphen
        real = joined in plain or _stem(joined) in stems
        if (la not in plain or lb not in plain or a.endswith(_LIGATURE_END)
                or (real and LINE_BREAK in sep and la not in _COMMON and lb not in _COMMON)):
            return a
        return m.group(0)

    def unglue(m: re.Match) -> str:
        low = m.group(0).lower()
        if low in glued and low not in plain:
            w = glued[low]
            return w[0].upper() + w[1:] if m.group(0)[0].isupper() else w
        return m.group(0)

    for _ in range(3):  # "suff er ing": one pass joins each word to the next, not chains
        new = _SPLIT_WORD_RE.sub(fix, text)
        if new == text:
            break
        text = new
    return _GLUED_RE.sub(unglue, text.replace(LINE_BREAK, " "))


def _mend_not_sign(m: re.Match) -> str:
    keep = vocab.keep_hyphen(m.group(1), m.group(2))
    return m.group(1) + ("-" if keep else "")


def normalize_spaces(text: str) -> str:
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = text.replace("ﬀ", "ff").replace("ﬃ", "ffi").replace("ﬄ", "ffl")
    text = re.sub(r"(\w+)¬\s*(?=([a-z]\w*))", _mend_not_sign, text)  # OCR'd line-break hyphen
    return re.sub(r"[ \t]+", " ", text).strip()


# --- scan noise ---------------------------------------------------------------------
# The OCR layer of an old scan reads specks, stains and bleed-through on blank pages as
# characters: "r-* V- >tr:", "' i ?*a ' s '.Ml-". Real text is mostly words.
_TITLES = {"mr", "mrs", "ms", "dr", "st", "pp", "ed", "eds", "vol", "no", "ch", "chs", "ff", "sr", "jr", "vs",
           "p", "op", "cf", "ie", "eg", "al", "et", "tr", "rev", "ibid", "loc", "viz"}
_SHORT_WORDS = {"a", "i", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it", "me", "my",
                "no", "of", "on", "or", "so", "to", "up", "us", "we", "am", "ad"}
_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.I)
_NUMERIC_RE = re.compile(r"\d{1,4}(?:[-–—,./:]\d{1,4})*")
_LINK_RE = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+\.\S+", re.I)
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’\-–—][A-Za-z]+)*")


def _is_wordlike(tok: str) -> bool:
    if _LINK_RE.match(tok.lstrip("(<[")):
        return True
    tok = re.sub(r"(?<=[.,;:!?”’\"')\]])\d{1,3}$", "", tok)  # a note mark glued on: 'civilization."1'
    core = tok.strip("'’\"“”‘()[]{}.,;:!?—–-_*\\")
    if not core:
        return False
    if _NUMERIC_RE.fullmatch(core):  # "7", "45-51", "155,160,161"
        return True
    if not _WORD_RE.fullmatch(core):
        return False
    letters = re.sub(r"[^A-Za-z]", "", core)
    if len(letters) >= 3:
        return bool(re.search(r"[aeiouyAEIOUY]", letters)) or bool(_ROMAN_RE.match(letters))
    low = letters.lower()
    if low in _TITLES or low in _SHORT_WORDS:
        return True
    # initials ("J. R. Lucas") and Roman numerals ("v", "II")
    if len(letters) == 1 and letters.isupper() and tok.rstrip(",;:)").endswith("."):
        return True
    return bool(_ROMAN_RE.match(letters)) and (letters.isupper() or letters in {"v", "x"})


def strip_scan_noise(text: str, keep_prefix: bool = False) -> str:
    """Return `text` unchanged if it is mostly words and "" if it is mostly junk. With
    `keep_prefix` (for headings), a real leading fragment is kept: "Part I _, - ' t" -> "Part I"."""
    plain = re.sub(r"</?sup>|[*_#>\\]", " ", text)
    toks = plain.split()
    if not toks:
        return text if "<sup>" in text else ""
    flags = [_is_wordlike(t) for t in toks]
    total = sum(len(t) for t in toks)
    good = sum(len(t) for t, f in zip(toks, flags) if f)
    # real text has something to say: a word of three letters, a link, or a number of two digits
    # (strings of "i", "if" and "4" are what specks look like to an OCR engine)
    has_content = "<sup>" in text or any(
        f and (_LINK_RE.match(t.lstrip("(<[")) or len(re.sub(r"[^A-Za-z]", "", t)) >= 3
               or len(re.sub(r"\D", "", t)) >= 2)
        for t, f in zip(toks, flags))
    if has_content and good >= 0.5 * total and (len(toks) == 1 or sum(not f for f in flags) <= 0.4 * len(toks)):
        return text
    if keep_prefix:
        run = []
        for t, f in zip(toks, flags):
            if not f:
                break
            run.append(t)
        if run and all(t[0].isupper() or t[0].isdigit() for t in run) \
                and any(re.fullmatch(r"[A-Za-z]{3,}", t.strip(".,;:")) for t in run):
            return " ".join(run)
    return ""
