"""Earmark Markdown -> EPUB 3.

Footnotes can be handled four ways, chosen at export time:

  endnotes         reference numbers link to a Notes section at the end of the book
                   (marked up with epub:type="noteref"/"endnote" so readers that
                   understand it can skip or pop up the notes)
  after_paragraph  each note is read right after the paragraph that cites it
  inline           each note is read in parentheses at the point it's cited
  omit             notes and reference numbers are removed
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import html
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from html.entities import name2codepoint
from io import BytesIO
from xml.etree import ElementTree as ET

from markdown_it import MarkdownIt

FOOTNOTE_MODES = {
    "endnotes": "Notes at the end (linked)",
    "after_paragraph": "Read after each paragraph",
    "inline": "Read where cited, in parentheses",
    "omit": "Leave notes out",
}

DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")
REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
PAGE_RE = re.compile(r"^\s*<!--\s*page\s+\d+\s*-->\s*$", re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
SPLIT_THRESHOLD = 150_000  # characters of body HTML before splitting into chapters


@dataclass
class ParsedDoc:
    meta: dict
    body: str
    notes: dict[str, str] = field(default_factory=dict)
    note_order: list[str] = field(default_factory=list)


def parse_front_matter(md: str) -> tuple[dict, str]:
    meta: dict = {}
    if md.startswith("---\n"):
        end = md.find("\n---", 4)
        if end != -1:
            for line in md[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    meta[k.strip().lower()] = v
            md = md[end + 4:].lstrip("\n")
    return meta, md


def parse(md: str) -> ParsedDoc:
    meta, md = parse_front_matter(md.replace("\r\n", "\n"))
    body_lines: list[str] = []
    notes: dict[str, str] = {}
    order: list[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = DEF_RE.match(line)
        if m:
            label, text = m.group(1), [m.group(2)]
            i += 1
            # continuation lines are indented; a blank line followed by an
            # indented line continues the note as a new paragraph
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith(("    ", "\t", "  ")) and nxt.strip():
                    text.append(nxt.strip())
                    i += 1
                elif not nxt.strip() and i + 1 < len(lines) and lines[i + 1].startswith(("    ", "\t")):
                    text.append("\n\n")
                    i += 1
                else:
                    break
            notes[label] = " ".join(text).replace(" \n\n ", "\n\n").strip()
            if label not in order:
                order.append(label)
            continue
        if not PAGE_RE.match(line):
            body_lines.append(line)
        i += 1
    body = COMMENT_RE.sub("", "\n".join(body_lines))
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    return ParsedDoc(meta, body, notes, order)


def _md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True, "xhtmlOut": True})


def _fix_entities(s: str) -> str:
    keep = {"lt", "gt", "amp", "quot", "apos"}

    def rep(m):
        name = m.group(1)
        if name in keep:
            return m.group(0)
        cp = name2codepoint.get(name)
        return f"&#{cp};" if cp else "&amp;" + name + ";"
    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", rep, s)


def _render(md_text: str) -> str:
    """Render Markdown to XHTML, falling back to escaping raw HTML if the user's
    edits left it malformed (only <sup>/<sub>/<i>/<em>/<b> survive the fallback)."""
    out = _fix_entities(_md().render(md_text))
    try:
        ET.fromstring(f"<div>{out}</div>")
        return out
    except ET.ParseError:
        pass
    allowed = re.compile(r"</?(sup|sub|i|em|b|strong)>")
    tokens: list[str] = []

    def stash(m):
        tokens.append(m.group(0))
        return f"⟦T{len(tokens) - 1}⟧"
    safe = allowed.sub(stash, md_text)
    out = MarkdownIt("commonmark", {"html": False, "xhtmlOut": True}).render(safe)
    out = re.sub("⟦T(\\d+)⟧", lambda m: tokens[int(m.group(1))], out)
    try:
        ET.fromstring(f"<div>{out}</div>")
        return out
    except ET.ParseError:
        return re.sub(r"</?(sup|sub|i|em|b|strong)>", "", out)


def _inline_note_md(text: str) -> str:
    # notes are embedded inside a paragraph: flatten paragraphs and block syntax
    text = text.replace("\n\n", " ")
    return re.sub(r"^(#+|>)\s*", "", text)


def _slug(text: str, used: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "section"
    base = "h-" + base
    s, n = base, 2
    while s in used:
        s, n = f"{base}-{n}", n + 1
    used.add(s)
    return s


@dataclass
class BuildResult:
    data: bytes
    body_html: str
    warnings: list[str]


def build_body_html(doc: ParsedDoc, mode: str) -> tuple[str, list[str], list[str]]:
    """Returns (body xhtml, labels in citation order, warnings)."""
    warnings: list[str] = []
    cited: list[str] = []
    missing: list[str] = []
    note_num: dict[str, int] = {}

    def num(label):
        if label not in note_num:
            note_num[label] = len(note_num) + 1
        return note_num[label]

    blocks = re.split(r"\n\s*\n", doc.body)
    out_blocks: list[str] = []
    for block in blocks:
        after: list[str] = []

        def ref(m):
            label = m.group(1)
            if label not in doc.notes:
                missing.append(label)
                return ""
            if label not in cited:
                cited.append(label)
            n = num(label)
            if mode == "endnotes":
                return f"⟦FN:{label}⟧"
            if mode == "inline":
                return f" (Footnote {n}: {_inline_note_md(doc.notes[label]).strip()})"
            if mode == "after_paragraph":
                after.append(f"⟦NOTE⟧ Footnote {n}: {_inline_note_md(doc.notes[label]).strip()}")
                return ""
            return ""  # omit

        block = REF_RE.sub(ref, block)
        out_blocks.append(block)
        out_blocks.extend(after)
    if missing:
        warnings.append("Reference(s) with no matching note were dropped: "
                        + ", ".join(sorted(set(missing))))

    body = _render("\n\n".join(out_blocks))
    body = body.replace("<p>⟦NOTE⟧ ", '<p class="note">')

    seen_refs: dict[int, int] = {}

    def noteref(m):
        label = m.group(1)
        n = num(label)
        seen_refs[n] = seen_refs.get(n, 0) + 1
        rid = f"ref-{n}" if seen_refs[n] == 1 else f"ref-{n}-{seen_refs[n]}"  # a note cited twice
        return (f'<sup><a epub:type="noteref" role="doc-noteref" id="{rid}" '
                f'href="notes.xhtml#note-{n}">{n}</a></sup>')
    body = re.sub("⟦FN:([^⟧]+)⟧", noteref, body)

    # heading ids for navigation
    used: set = set()

    def hid(m):
        level, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", inner)
        return f'<h{level} id="{_slug(html.unescape(text), used)}">{inner}</h{level}>'
    body = re.sub(r"<h([1-6])>(.*?)</h\1>", hid, body, flags=re.S)
    # remember numbering for the notes file
    doc.meta["_note_num"] = note_num
    return body, cited, warnings


def _xhtml(title: str, body: str, lang: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'xml:lang="{lang}" lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n'
        f"<title>{html.escape(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="../style.css"/>\n</head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


CSS = """
body { font-family: serif; line-height: 1.45; margin: 0 4%; }
h1 { font-size: 1.6em; text-align: center; margin: 1.2em 0 0.4em; }
h2 { font-size: 1.3em; margin: 1.4em 0 0.4em; }
h3 { font-size: 1.1em; margin: 1.2em 0 0.3em; }
p { margin: 0 0 0.6em; text-indent: 0; }
blockquote { margin: 0.8em 2em; }
sup { font-size: 0.7em; line-height: 0; }
sup a { text-decoration: none; }
p.note { font-size: 0.9em; margin: 0.2em 1.5em 0.8em; color: #444; }
section.endnotes ol { padding-left: 0; list-style: none; }
section.endnotes li { margin-bottom: 0.6em; font-size: 0.92em; }
section.endnotes li a.num { text-decoration: none; font-weight: bold; }
"""


def _split_sections(body: str) -> list[str]:
    if len(body) < SPLIT_THRESHOLD:
        return [body]
    parts = re.split(r"(?=<h[12][ >])", body)
    sections: list[str] = []
    for p in parts:
        if sections and len(sections[-1]) < 20_000:
            sections[-1] += p
        elif p.strip():
            sections.append(p)
    return sections or [body]


def build_epub(md: str, mode: str = "endnotes", title: str | None = None,
               author: str | None = None, lang: str | None = None) -> BuildResult:
    if mode not in FOOTNOTE_MODES:
        raise ValueError(f"unknown footnote mode {mode!r}")
    doc = parse(md)
    title = (title or doc.meta.get("title") or "Untitled").strip()
    author = (author if author is not None else doc.meta.get("author", "")).strip()
    lang = (lang or doc.meta.get("lang") or "en").strip()
    body, cited, warnings = build_body_html(doc, mode)
    note_num: dict = doc.meta.pop("_note_num")

    sections = _split_sections(body)
    files = [f"part{i + 1:03d}.xhtml" for i in range(len(sections))]

    # notes file (endnotes mode): cited notes in order, then any never cited
    notes_html = None
    if mode == "endnotes" and doc.notes:
        uncited = [l for l in doc.note_order if l not in cited]
        if uncited:
            warnings.append(f"{len(uncited)} note(s) are never cited; they're listed at the end of the notes.")
        items = []
        where = {}
        for fi, sec in zip(files, sections):
            for m in re.finditer(r'id="ref-(\d+)"', sec):
                where[m.group(1)] = fi
        for label in cited + uncited:
            if label in note_num:
                n = note_num[label]
            else:
                n = len(note_num) + 1
                note_num[label] = n
            text_html = _render(doc.notes[label])
            back = where.get(str(n))
            num = (f'<a class="num" href="{back}#ref-{n}">{n}.</a>' if back
                   else f'<span class="num">{n}.</span>')
            # put the number inside the first paragraph
            text_html = text_html.replace("<p>", f"<p>{num} ", 1) if text_html.startswith("<p>") \
                else f"<p>{num}</p>{text_html}"
            items.append(f'<li id="note-{n}" epub:type="endnote">{text_html}</li>')
        notes_html = ('<section class="endnotes" epub:type="endnotes" role="doc-endnotes">\n'
                      '<h2 id="notes">Notes</h2>\n<ol>\n' + "\n".join(items) + "\n</ol>\n</section>")

    # navigation from headings
    nav_points = []  # (level, file, id, text)
    for fi, sec in zip(files, sections):
        for m in re.finditer(r'<h([1-3]) id="([^"]+)">(.*?)</h\1>', sec, re.S):
            text = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            text = re.sub(r"\s+", " ", html.unescape(text))
            nav_points.append((int(m.group(1)), fi, m.group(2), text))
    if notes_html:
        nav_points.append((2, "notes.xhtml", "notes", "Notes"))
    if not nav_points:
        nav_points.append((1, files[0], "", title))

    uid = "urn:uuid:" + str(uuid.UUID(hashlib.md5((title + "|" + author + "|" + md[:2000]).encode()).hexdigest()))
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for i, f in enumerate(files):
        manifest.append(f'<item id="p{i + 1}" href="text/{f}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="p{i + 1}"/>')
    if notes_html:
        manifest.append('<item id="notes" href="text/notes.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="notes"/>')

    esc = html.escape
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{uid}</dc:identifier>
    <dc:title>{esc(title)}</dc:title>
    {f'<dc:creator>{esc(author)}</dc:creator>' if author else ''}
    <dc:language>{esc(lang)}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
    <meta name="generator" content="Earmark"/>
  </metadata>
  <manifest>
    {chr(10).join('    ' + m for m in manifest).strip()}
  </manifest>
  <spine toc="ncx">
    {chr(10).join('    ' + s for s in spine).strip()}
  </spine>
</package>
"""

    def nav_list(points):
        out, stack = [], []
        for level, f, hid, text in points:
            href = f"text/{f}" + (f"#{hid}" if hid else "")
            while stack and stack[-1] > level:
                out.append("</li></ol>")
                stack.pop()
            if stack and stack[-1] == level:
                out.append("</li>")
            elif not stack or stack[-1] < level:
                out.append("<ol>")
                stack.append(level)
            out.append(f'<li><a href="{href}">{esc(text)}</a>')
        while stack:
            out.append("</li></ol>")
            stack.pop()
        return "".join(out)

    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">\n'
        f'<head><meta charset="utf-8"/><title>{esc(title)}</title></head>\n<body>\n'
        f'<nav epub:type="toc" id="toc"><h1>Contents</h1>{nav_list(nav_points)}</nav>\n</body>\n</html>\n'
    )
    ncx_points = "".join(
        f'<navPoint id="np{i + 1}" playOrder="{i + 1}"><navLabel><text>{esc(t)}</text></navLabel>'
        f'<content src="text/{f}{"#" + h if h else ""}"/></navPoint>'
        for i, (_, f, h, t) in enumerate(nav_points))
    ncx = (f'<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{esc(title)}</text></docTitle>'
           f'<navMap>{ncx_points}</navMap></ncx>\n')

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>\n', compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for f, sec in zip(files, sections):
            z.writestr(f"OEBPS/text/{f}", _xhtml(title, sec, lang), compress_type=zipfile.ZIP_DEFLATED)
        if notes_html:
            z.writestr("OEBPS/text/notes.xhtml", _xhtml("Notes", notes_html, lang),
                       compress_type=zipfile.ZIP_DEFLATED)
    preview = body + ("\n" + notes_html if notes_html else "")
    return BuildResult(buf.getvalue(), preview, warnings)
