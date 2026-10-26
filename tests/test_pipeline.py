import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from refolio import cleanup
from refolio.assemble import assemble
from refolio.backends import builtin, marker_backend
from refolio.convert import convert
from refolio.epub import FOOTNOTE_MODES, build_epub, parse
from refolio.model import Block, Extracted

FIX = Path(__file__).parent / "fixtures"
TWO_COL = FIX / "two_column_journal.pdf"
WORD = FIX / "word_single_column.pdf"


def body_paragraphs(md):
    doc = parse(md)
    return [p for p in doc.body.split("\n\n") if p and not p.startswith("#")]


# --- built-in converter -------------------------------------------------------

def test_two_column_reading_order_and_footnotes():
    a = convert(TWO_COL)
    md = a.markdown
    # running head, journal footer and page numbers are gone
    assert "Journal of Imaginary Epistemology" not in md
    assert "downloaded from" not in md.lower()
    # the paragraph split across the column break is rejoined
    assert "two rival theses.[^3] Both parties agree that a hearer" in md
    # left column comes before right column, section 2 before 2.1
    assert md.index("## 2 The Reductionist Picture") < md.index("### 2.1 A worry about children")
    # every note found and linked, including the one continued in the next column
    assert a.stats["notes"] == 5 and a.stats["linked"] == 5 and a.stats["unmatched_refs"] == 0
    note3 = parse(md).notes["3"]
    assert note3.startswith("This objection") and note3.endswith("in the final section.")
    assert "<sup>" not in md


def test_word_footnotes_same_size_as_body():
    a = convert(WORD)
    assert a.stats["notes"] == 5 and a.stats["linked"] == 5
    doc = parse(a.markdown)
    assert doc.meta["title"] == "On the Ethics of Belief"
    assert doc.notes["1"].startswith("The locus classicus")
    assert "eighteenth-century" in doc.notes["1"]


def test_book_style_cover_page_alternating_heads_long_note():
    a = convert(FIX / "book_style_jstor.pdf")
    md = a.markdown
    assert "JSTOR" not in md and "Stable URL" not in md
    assert "A. N. Author\n" in md and "118" not in md and "119" not in md
    assert a.stats["notes"] == a.stats["linked"] == 6
    assert parse(md).meta["author"] == "A. N. Author"


def test_title_and_author_detected():
    doc = parse(convert(TWO_COL).markdown)
    assert doc.meta["title"] == "Trusting the Word of Others"
    assert doc.meta["author"] == "A. N. Author"


def test_xy_cut_two_columns_with_spanning_title():
    def b(x0, y0, x1, y1, lines=3):
        return SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1, lines=[0] * lines, name=(x0, y0))
    title = b(200, 50, 400, 70, 1)
    left = [b(50, 100, 290, 200), b(50, 205, 290, 300)]
    right = [b(310, 100, 550, 150), b(310, 202, 550, 300)]  # gap lines up with the left gap
    order = builtin.xy_cut([right[1], left[1], title, right[0], left[0]])
    assert order == [title, left[0], left[1], right[0], right[1]]


# --- assembly ------------------------------------------------------------------

def test_note_continuation_and_orphans():
    ex = Extracted([
        Block("para", "Claim one.<sup>1</sup> Claim two.<sup>2</sup>", 1),
        Block("footnote", "<sup>1</sup>First note that runs", 1),
        Block("footnote", "on to the next column.", 1),
        Block("footnote", "<sup>2</sup>Second note.", 1),
        Block("footnote", "<sup>7</sup>Never cited.", 2),
        Block("para", "A mark with no note.<sup>9</sup>", 2),
    ], 2, title="T")
    a = assemble(ex)
    doc = parse(a.markdown)
    assert doc.notes["1"] == "First note that runs on to the next column."
    assert doc.notes["2"] == "Second note."
    assert "Never cited." in doc.notes.values()
    assert "<sup>9</sup>" in a.markdown
    assert len(a.warnings) == 2


def test_paragraph_merge_across_pages_keeps_hyphen_logic():
    ex = Extracted([
        Block("para", "The argument for this philo-", 1),
        Block("para", "sophical thesis is simple.", 2),
    ], 2, title="T")
    md = assemble(ex).markdown
    assert "philosophical thesis" in md


# --- EPUB -------------------------------------------------------------------------

SAMPLE = """---
title: "A Paper"
author: "Some One"
---

<!-- page 1 -->

# A Paper

First claim.[^1] Second claim.[^2]

> A quotation.[^1]

<!-- page 2 -->

## Section & more

Text with <sup>oops</sup> and an *emphasis* and &nbsp;entity.

[^1]: The first note.

[^2]: The second note, with *style*.

[^9]: Never cited.
"""


@pytest.mark.parametrize("mode", list(FOOTNOTE_MODES))
def test_epub_structure(mode):
    r = build_epub(SAMPLE, mode)
    z = zipfile.ZipFile(__import__("io").BytesIO(r.data))
    names = z.namelist()
    assert names[0] == "mimetype" and z.read("mimetype") == b"application/epub+zip"
    body = z.read("OEBPS/text/part001.xhtml").decode()
    assert "page 1" not in body
    if mode == "endnotes":
        assert 'epub:type="noteref"' in body
        notes = z.read("OEBPS/text/notes.xhtml").decode()
        assert "The first note." in notes and "Never cited." in notes
    elif mode == "inline":
        assert "(Footnote 1: The first note.)" in body
    elif mode == "after_paragraph":
        assert '<p class="note">Footnote 2: The second note, with <em>style</em>.</p>' in body
    else:
        assert "note" not in body.lower()


@pytest.mark.skipif(shutil.which("java") is None, reason="epubcheck needs Java")
def test_epubcheck_valid(tmp_path):
    pytest.importorskip("epubcheck")
    for mode in FOOTNOTE_MODES:
        for src in (SAMPLE, convert(TWO_COL).markdown):
            out = tmp_path / f"{mode}.epub"
            out.write_bytes(build_epub(src, mode).data)
            res = subprocess.run([sys.executable, "-m", "epubcheck", str(out)], capture_output=True, text=True)
            assert res.returncode == 0, res.stdout + res.stderr


def test_malformed_html_falls_back():
    r = build_epub("# T\n\nBroken <b>tag and <div> here.\n", "endnotes")
    assert "Broken" in r.body_html


def test_long_document_is_split_and_notes_link_back():
    para = "Words " * 400
    parts = [f"## Chapter {i}\n\n{para}[^{i}]\n\n[^{i}]: Note {i}." for i in range(1, 80)]
    md = "---\ntitle: \"Book\"\n---\n\n# Book\n\n" + "\n\n".join(parts)
    r = build_epub(md, "endnotes")
    z = zipfile.ZipFile(__import__("io").BytesIO(r.data))
    parts_files = [n for n in z.namelist() if re.search(r"part\d+\.xhtml", n)]
    assert len(parts_files) > 1
    notes = z.read("OEBPS/text/notes.xhtml").decode()
    assert 'href="part001.xhtml#ref-1"' in notes
    assert re.search(r'href="part0(0[2-9]|[1-9]\d)\.xhtml#ref-79"', notes)


# --- cleanup ---------------------------------------------------------------------

def test_cleanup_ops():
    md = ("---\ntitle: \"T\"\n---\n\n<!-- page 1 -->\n\nIt runs on to the\n\n<!-- page 2 -->\n\n12\n\n"
          "next page (Smith 1990, 23) and see https://example.org/x for the philo- sophy.\n\n"
          "HEAD 1\n\nHEAD 2\n\nHEAD 3\n")
    md, n = cleanup.run("page_numbers", md); assert n == 1
    md, n = cleanup.run("repeated", md); assert n == 3
    md, n = cleanup.run("join_paragraphs", md); assert n == 1
    md, n = cleanup.run("citations", md); assert n == 1
    md, n = cleanup.run("urls", md); assert n == 1
    md, n = cleanup.run("hyphens", md); assert n == 1
    assert "It runs on to the next page and see for the philosophy." in md
    assert md.startswith("---\ntitle:")


# --- Marker adapter (no models needed) ----------------------------------------------

def test_marker_json_mapping():
    def node(bt, html="", children=None, bbox=(0, 0, 1, 1)):
        return SimpleNamespace(block_type=bt, html=html, children=children, bbox=bbox)
    page = node("Page", children=[
        node("PageHeader", "<p>Journal</p>"),
        node("SectionHeader", "<h1>A Title</h1>"),
        node("SectionHeader", "<h2>1 Intro</h2>"),
        node("Text", "<p>We read <i>Meditations </i>closely.<sup>1</sup></p>"),
        node("ListGroup", children=[node("ListItem", "<li>first</li>")]),
        node("Picture", ""),
        node("Footnote", "<p><sup>1</sup>The note.</p>"),
    ])
    blocks, warns = [], []
    marker_backend._walk(page, 1, blocks, warns)
    kinds = [b.kind for b in blocks]
    assert kinds == ["heading", "heading", "para", "list", "footnote"]
    assert blocks[2].text == "We read *Meditations* closely.<sup>1</sup>"
    md = assemble(Extracted(blocks, 1, title="A Title")).markdown
    assert "closely.[^1]" in md and "[^1]: The note." in md


# --- web app -------------------------------------------------------------------------

def test_web_app_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from refolio.server import create_app
    import time

    c = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    assert c.get("/").status_code == 200
    info = c.get("/api/info").json()
    assert {e["id"] for e in info["engines"]} == {"builtin", "marker"}
    r = c.post("/api/projects", files={"file": ("p.pdf", TWO_COL.read_bytes(), "application/pdf")},
               data={"engine": "builtin"})
    pid = r.json()["id"]
    for _ in range(100):
        d = c.get(f"/api/projects/{pid}").json()
        if d["status"] != "working":
            break
        time.sleep(0.1)
    assert d["status"] == "done", d
    assert c.get(f"/api/projects/{pid}/pages/1.png").headers["content-type"] == "image/png"
    md = d["markdown"] + "\n\nAn added line.\n"
    assert c.put(f"/api/projects/{pid}/markdown", json={"markdown": md}).json()["ok"]
    r = c.post(f"/api/projects/{pid}/export", json={"markdown": md, "mode": "endnotes", "title": "X Y"})
    assert r.headers["content-type"] == "application/epub+zip"
    assert r.content[:2] == b"PK"
    assert c.post("/api/projects", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert c.get("/api/projects").json()[0]["id"] == pid


def test_server_refuses_foreign_hosts_and_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from refolio.server import create_app

    c = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    assert c.get("/api/info").status_code == 200
    assert c.get("/api/info", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
    assert c.get("/api/info", headers={"Host": "localhost:8765"}).status_code == 200
    assert c.get("/api/info", headers={"Host": "evil.example:8765"}).status_code == 403  # DNS rebinding
    r = c.delete("/api/projects/abcdef123456", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post("/api/projects", headers={"Origin": "http://localhost:9999"}).status_code == 403


def test_ocr_garbled_and_fused_notes_are_split_by_sequence():
    from refolio.assemble import _collect_notes
    from refolio.model import Block

    text = ("i. Richard Sorabji, Animal Minds (Ithaca, 1993). <sup>2.</sup> England may be. See p. 63. "
            "io. is not next. <sup>3.</sup> Rodd, p. 62.")
    notes = _collect_notes([Block("footnote", text, 13)])
    assert [n.mark for n in notes] == ["1", "2", "3"]
    assert notes[1].text.startswith("England") and notes[2].text.startswith("Rodd")
    # an OCR lead that breaks the sequence stays part of the previous note
    notes = _collect_notes([Block("footnote", "8. Foo. <sup>9.</sup> Bar, p. 63. io. Baz. <sup>11.</sup> Qux.", 1)])
    assert [n.mark for n in notes] == ["8", "9", "10", "11"]


def test_initials_are_not_ocr_note_marks_and_not_sign_hyphen_is_mended():
    from refolio.assemble import _collect_notes
    from refolio.model import Block
    from refolio.textutil import join_hyphenated, normalize_spaces

    notes = _collect_notes([Block("footnote", "4. Foo, p. 1.", 1), Block("footnote", "S. Harrison, Bar.", 1),
                            Block("footnote", "B. Russell, Baz.", 1)])
    assert [n.mark for n in notes] == ["4"] and "Harrison" in notes[0].text and "Russell" in notes[0].text
    assert join_hyphenated("dabbler of intellec\u00ac", "tual pursuits") == "dabbler of intellectual pursuits"
    assert normalize_spaces("the\u00ac ology") == "theology"


def test_running_head_with_page_number_and_title_is_dropped():
    from refolio.backends.builtin import RUNNING_HEAD_RE

    for t in ("xii PREFACE", "PREFACE xiii", "XIV PREFACE", "112 The Moral Universe"):
        assert RUNNING_HEAD_RE.match(t), t
    for t in ("CHAPTER 3", "Part 2", "This is a sentence 3.", "Is it so? 4"):
        assert not RUNNING_HEAD_RE.match(t), t


# --- text-matrix scaled PDFs (Word export: pdftext reports every font size as 1.0) ---

def _chars(text, h, x=0.0):
    out = []
    for ch in text:
        out.append({"char": ch, "bbox": [x, 100.0, x + 5, 100.0 + h]})
        x += 5
    return out


def test_bogus_font_size_uses_char_heights_for_superscripts():
    span = {"text": "civilization.”1 One", "chars": _chars("civilization.”", 13.3) + _chars("1", 8.8)
            + _chars(" One", 13.3)}
    runs = builtin._bogus_size_runs(span, 13.3, False, False)
    assert [(r.text, r.sup) for r in runs] == [("civilization.”", False), ("1", True), (" One", False)]
    assert runs[0].size == pytest.approx(13.3) and runs[1].size == pytest.approx(8.8)


def test_block_split_where_print_size_drops_and_fused_notes_separate():
    R, L = builtin.Run, builtin.Line
    body = L([R("A body line of text.", 13.3)], (90, 600, 525, 613))
    note = L([R("7", 7.2, sup=True), R(" Apuleius, XI, 5. ", 11.2), R("8", 7.2, sup=True),
              R(" Tuzuk-i-Jahangiri", 11.2)], (90, 697, 482, 720))
    tb = builtin.TBlock([body, note], (90, 600, 525, 720), 13)
    parts = builtin._split_by_size(tb)
    assert [len(p.lines) for p in parts] == [1, 1]
    lines = builtin._split_at_note_marks(note)
    assert [l.plain.strip() for l in lines] == ["7 Apuleius, XI, 5.", "8 Tuzuk-i-Jahangiri"]


def test_plain_digit_marks_and_fused_notes_link():
    # what Marker returns for a Word export: marks lose their superscript, notes fuse
    blocks = [
        Block("para", 'our own civilization."1 One cannot ask. Then more text."2 Done.', 8),
        Block("footnote", "<sup> 1</sup> Ruth Benedict, *Patterns*, p. 5.", 8),
        Block("footnote", "<sup>2</sup> Apuleius, XI, 5. <sup>3</sup> Tuzuk, p. 356.", 8),
    ]
    a = assemble(Extracted(blocks, 8))
    assert a.stats["notes"] == 3 and a.stats["linked"] == 2 and a.stats["unmatched_refs"] == 0
    assert 'civilization."[^1] One' in a.markdown and 'more text."[^2] Done' in a.markdown
    assert "[^3]: Tuzuk, p. 356." in a.markdown
    # a year or ordinary number is never taken for a mark
    b2 = [Block("para", "It was 1934. See chapter 1 and p. 5.", 3),
          Block("footnote", "<sup>1</sup> A note.", 3)]
    assert assemble(Extracted(b2, 3)).stats["linked"] == 0


def _glyph(ch, x0, x1):
    return {"char": ch, "bbox": [x0, 100.0, x1, 108.0]}


def test_ligature_ghost_spaces_dropped_but_real_spaces_kept():
    # "Th" and "ff" are two glyphs sharing one box, followed by a space inside that box. After
    # "Th" the next letter butts up against the ligature (ghost); after "Jeff" it is a word
    # apart (real). A line-end soft hyphen arrives as \x02.
    chars = [_glyph("T", 0, 9), _glyph("h", 0, 9), _glyph(" ", 4, 6), _glyph("i", 9, 11),
             _glyph(" ", 11, 13), _glyph("J", 13, 16), _glyph("e", 16, 19), _glyph("f", 19, 22),
             _glyph("f", 19, 22), _glyph(" ", 18, 20), _glyph("M", 25, 32), _glyph("\x02", 32, 34), _glyph("\r", 34, 34),
             _glyph("\n", 34, 34)]
    assert builtin._span_text({"text": "", "chars": chars}) == "Thi Jeff M-"


def test_italic_titles_keep_their_spaces_and_notes_start_at_numbers_only_after_a_sentence():
    R, L = builtin.Run, builtin.Line
    lines = [L([R("*", 9), R("Farm", 9, italic=True)], (0, 0, 1, 1)),
             L([R("Animal Welfare", 9, italic=True), R(" (Ames), ", 9), R("Eggs", 9, italic=True)], (0, 0, 1, 1)),
             L([R("and", 9, italic=True)], (0, 0, 1, 1)), L([R("Moral", 9, italic=True)], (0, 0, 1, 1))]
    assert "*and Moral*" in builtin._lines_to_text(lines[2:])
    assert builtin._lines_to_text([L([R("end.", 9), R(" ", 9), R("3", 6, sup=True), R(" next", 9)], (0, 0, 1, 1))]) \
        == "end.<sup>3</sup> next"
    # an italic title opening a note is emphasis, not an asterisk mark
    from refolio.assemble import _collect_notes
    notes = _collect_notes([Block("footnote", "<sup>19</sup> Rollin, *Farm*", 8),
                            Block("footnote", "*Animal Welfare* (Ames, 1995).", 8)])
    assert len(notes) == 1 and notes[0].text.endswith("(Ames, 1995).")


def test_mend_split_words_uses_lexicon_and_line_breaks():
    from refolio.textutil import LINE_BREAK as LB, mend_split_words, word_set
    words = word_set("This paper offers a qualified view of single-file and equal-consideration; it extends. "
                     "Prudentially speaking, we are off the mark; anyone can see it, any day, one by one. Parfit and Jeff disagree.")
    fix = lambda t: mend_split_words(t, words)
    assert fix("Th is paper off ers a qualifi ed view") == "This paper offers a qualified view"
    assert fix("'Th is' and Parfi t, Jeff Parfit") == "'This' and Parfit, Jeff Parfit"
    assert fix("we are off the mark; any one can see") == "we are off the mark; any one can see"
    # a line break that hid the hyphen, or Marker's own dehyphenation of a compound
    assert fix(f"it ex{LB}tends, pruden{LB}tially, in a singlefi le chute") == "it extends, prudentially, in a single-file chute"
    assert fix(f"equal{LB}consideration, any{LB}one") == "equal consideration, any one"
    assert fix("") == ""


def test_marker_style_notes_are_numbered_by_sequence_and_marks_found():
    # Marker keeps no superscripts: marks are plain, spaced digits; notes fuse, one loses its number
    blocks = [
        Block("para", "animals lack moral status. 3 This may be due to accidents, respectively 4 ; and so on.", 2),
        Block("para", "Some deny it, 5 and others do not. Done. 6", 2),
        Block("footnote", "<sup>2</sup> First note, p. 70-80). But for factual", 2),
        Block("footnote", "claims. 3 *See*, e.g., Tom Regan. 4  Peter Singer, *Animal Liberation*.", 2),
        Block("footnote", "<sup> </sup>Fifth note, Part I. <sup>6</sup> Sixth note.", 2),
    ]
    a = assemble(Extracted(blocks, 2))
    assert a.stats["notes"] == 5 and a.stats["linked"] == 4 and a.stats["unmatched_refs"] == 0
    assert "status.[^1] This" in a.markdown and "respectively[^2]; and" in a.markdown
    assert "[^1]: *See*, e.g., Tom Regan." in a.markdown and "[^2]: Peter Singer, *Animal Liberation*." in a.markdown
    assert "[^3]: Fifth note, Part I." in a.markdown and "[^4]: Sixth note." in a.markdown
    # "ch. 3 and" is not a mark
    b2 = [Block("para", "See ch. 3 and p. 5.", 1), Block("footnote", "<sup>3</sup> A note.", 1)]
    assert assemble(Extracted(b2, 1)).stats["linked"] == 0


def test_marker_footnote_fixes_in_html_and_by_box():
    def node(bt, html="", children=None, bbox=(0, 0, 1, 1)):
        return SimpleNamespace(block_type=bt, html=html, children=children, bbox=bbox)
    # an entity cut in two by </sup>, and a <sup> around ordinary text
    assert marker_backend.html_to_inline("<p>x <sup> 30 &amp;#</sup>x27;Position (<sup>(1990).</sup> More</p>") \
        == "x <sup> 30 </sup>'Position ((1990). More"
    assert marker_backend.html_to_inline("<p>on <i>Title</i> , and ( <i>Book</i> )</p>") == "on *Title*, and (*Book* )"
    # a Text block on exactly the box of a Footnote is the tail of a note
    page = node("Page", children=[
        node("Text", "<p>Body text.</p>", bbox=(54, 80, 371, 156)),
        node("Text", "<p>tail of a note (New York: Oxford</p>", bbox=(54, 458, 371, 488)),
        node("Footnote", "<p></p>", bbox=(54, 458, 370, 488)),
        node("Footnote", "<p>University Press, 2000).</p>", bbox=(54, 482, 371, 529)),
    ])
    blocks = []
    marker_backend._walk(page, 1, blocks, [], marker_backend._footnote_boxes(page, []))
    assert [b.kind for b in blocks] == ["para", "footnote", "footnote"]


def test_scan_noise_is_dropped_but_real_text_survives():
    from refolio.textutil import strip_scan_noise as clean
    # what an OCR engine makes of specks and stains on a blank page
    for junk in ("r-* V- >tr:", "' i ?*a ' s '.Ml-", "i IV . U-", "if. i if t.. >", "I", "4", "-.-n;",
                 "•\" 1 1 wt*...; «... Pr.iTE . - > 4r"):
        assert clean(junk) == "", junk
    assert clean("Part I _, - ' t", keep_prefix=True) == "Part I"
    assert clean("Part I _, - ' t") == ""
    # real text: prose with glued marks and abbreviations, index entries, links, lone marks, headings
    for real in ('our own civilization."1 One cannot ask. Then more text."2 Done.',
                 "Exegesis, critical, 45-51, 60-2, 86-7, 90, 155,160,161,162",
                 "Mr J. R. Lucas and Mr M. R. McLean kindly read the greater part.",
                 "28. See chs. 4—5.", "Op. cit., pp. 112—13.", "<sup>*</sup>", "Notes",
                 "American Dietetic Association, ‘Vegetarian Diets’ (http://www.eatright.org/cps/rde/xchg/ ada/hs.xsl)."):
        assert clean(real) == real, real
    blocks = [Block("heading", "Part I _, - ' t", 19, 2), Block("para", "' i ?*a ' s '.Ml-", 19),
              Block("para", "Real prose about the argument.", 20)]
    a = assemble(Extracted(blocks, 20))
    assert "###### " not in a.markdown and "## Part I\n" in a.markdown
    assert "?*a" not in a.markdown and "Real prose" in a.markdown
    assert any("scanner noise" in w for w in a.warnings)


def test_ligature_font_codes_are_read_from_glyph_names():
    # a PDF's ligature subset numbers its glyphs per page: "!" is fi here, Th on another page
    assert builtin._glyph_text("f_f_i") == "ffi" and builtin._glyph_text("T_h") == "Th"
    assert builtin._glyph_text("onehalf") == "½" and builtin._glyph_text("uni00A0") == " "
    assert builtin._glyph_text("A") is None
    table = {"!": "fi", "#": "Th"}
    wide = {"char": "!", "bbox": [10.0, 0.0, 16.5, 10.0]}
    span = {"text": "!", "chars": [wide]}
    builtin._fix_ligature_span(span, table)
    assert span["text"] == "fi" and span["chars"][0]["char"] == "fi"
    narrow = {"text": "!", "chars": [{"char": "!", "bbox": [10.0, 0.0, 12.5, 10.0]}]}  # a real "!"
    builtin._fix_ligature_span(narrow, table)
    assert narrow["text"] == "!"


def test_marker_ligature_codes_are_repaired_from_the_lexicon():
    from refolio.textutil import mend_split_words, word_set
    words = word_set("The justified justification offer offers affirming difficult The Theory theistic "
                     "significance qualification a&b")
    fix = lambda t: mend_split_words(t, words)
    assert fix('"e justi!ed claim o$er a#rming di"cult') == "The justified claim offer affirming difficult"
    assert fix("justi! cation, signi# cance and justi- !ed") == "justification, significance and justified"
    assert fix('the *!**eory* of "#e theistic') == 'the *Theory* of "The theistic'
    # real punctuation is left alone
    assert fix('a&b, "quoted" text! and 100% sure, #1') == 'a&b, "quoted" text! and 100% sure, #1'
