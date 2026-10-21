# Refolio

Refolio turns PDFs of papers into clean EPUBs for listening in text-to-speech apps such as ElevenReader.

PDF-to-EPUB tools are built for reading on a screen, and most of what goes wrong with them goes wrong loudly when the text is read aloud:

- two columns get read across instead of down,
- running heads and page numbers are read on every page,
- footnotes land in the middle of sentences.

Refolio fixes these, then shows you the text next to the original pages so you can give it a quick once-over before exporting.

It was written for philosophy and humanities papers: mostly text, sometimes two columns, lots of footnotes. It doesn't try to handle heavy math, tables or figures.

## What it does

1. **Converts the PDF** to plain, editable text:
   - puts columns in reading order,
   - removes running heads, page numbers and "This content downloaded from…" lines,
   - skips JSTOR cover pages,
   - rejoins paragraphs and words split across lines, columns and pages,
   - links each footnote mark to its note, including notes that continue in the next column or onto the next page.
2. **Lets you review it** side by side with the original page. One-click tools fix the usual leftovers, and a notice bar points out anything that needs a look, such as a footnote number that didn't match a note.
3. **Exports an EPUB 3** with your choice of footnote handling:

   | Option | What you hear |
   |---|---|
   | Notes at the end (linked) | The text straight through, then the notes. Reference numbers link to the notes in reading apps. |
   | Read after each paragraph | Each note right after the paragraph that cites it. |
   | Read where cited | Each note in parentheses at the point it's cited. |
   | Leave notes out | Just the text. |

   The "Listening order" tab shows exactly what will be read with each option.

Everything runs on your computer. Nothing is uploaded.

## Install and run on a Mac

**Easiest:** download this folder and double-click **`Refolio.command`**.

- The first time, macOS may say it can't verify the developer. If so, right-click the file, choose **Open**, then **Open** again. You only have to do this once.
- The first run spends a minute or two installing what it needs: [uv](https://docs.astral.sh/uv/), which brings its own Python.
- After that, Refolio opens in your browser.
- Keep the Terminal window open while you use it. Close it to quit.

**From the Terminal**, if you prefer:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh                      # once, if you don't have uv
uv tool install --python 3.12 git+https://github.com/jeremiahcarey/refolio
refolio
```

Refolio also runs on Windows and Linux the same way (`uv tool install …`, then `refolio`), though it has mainly been designed for macOS.

## Two converters

| | Fast (built-in) | Accurate (Marker) |
|---|---|---|
| How it works | Reads the PDF's own text layer and works out the layout | Uses [Marker](https://github.com/datalab-to/marker)'s layout-detection models |
| Good for | Born-digital papers: Word exports, LaTeX, most journal PDFs | Unusual layouts, scans, anything the fast converter gets wrong |
| Speed | A second or two per paper | Slower; the first run downloads its models |
| Install | Included | Double-click **`Refolio with Marker.command`** instead, or `uv tool install "refolio[marker] @ git+…"`. This adds PyTorch, several GB. |

Try the fast converter first. If a paper comes out badly, choose **Convert again** and switch to Marker.

Some notes on Marker:

- **Scanned PDFs:** on a Mac, Marker's OCR runs through llama.cpp. Install [Homebrew](https://brew.sh) and run `brew install llama.cpp`. Without it, Refolio still uses Marker but turns OCR off and tells you so.
- **Licensing:** Marker's code is Apache-2.0. Its model weights are free for research, personal use and organizations under $5M in funding or revenue. See [Marker's README](https://github.com/datalab-to/marker#commercial-usage) for commercial terms.

## Reviewing and editing

The left pane is the whole document as Markdown. You can edit anything:

```markdown
---
title: "Trusting the Word of Others"
author: "A. N. Author"
---

<!-- page 1 -->

# Trusting the Word of Others

## 1 Introduction

When we accept what another person tells us …ourselves.[^1] This familiar fact …

> A block quotation.

[^1]: The note text. Notes sit at the end of the page they came from.
```

- `[^1]` marks a footnote reference, and `[^1]: …` is the note itself. To link a stray number by hand, replace `<sup>4</sup>` with `[^4]` and make sure a `[^4]: …` line exists.
- The `<!-- page N -->` lines keep the original page view in step with where you are in the text. They aren't included in the EPUB.
- `*italic*`, `**bold**`, `#` headings and `>` quotations work as in any Markdown.
- Edits save automatically. Your documents are kept in `~/Library/Application Support/Refolio` (on other systems, `~/.refolio`).

### Cleanup tools

| Tool | What it does |
|---|---|
| Fix › Split paragraphs | Rejoins a paragraph broken by a page or column break: the first part ends mid-sentence and the next starts in lower case |
| Fix › Hyphenation | Rejoins words split across lines ("philo- sophy"), keeping real compounds ("self-evident") |
| Remove › Page numbers | Deletes lines that are only a page number |
| Remove › Repeated lines | Deletes short lines that occur three or more times, such as a running head that slipped through |
| Remove › Stray superscripts | Deletes superscripts that didn't match any footnote |
| Remove › Citations | Deletes author-year citations like "(Smith 1990, 23)", which are tedious to listen to |
| Remove › Web addresses | Deletes URLs and DOIs |

Every tool can be undone with ⌘Z. **Find and replace** (⌘F) supports regular expressions for anything else.

## Command line

```sh
refolio convert paper.pdf                      # writes paper.epub (notes at the end)
refolio convert paper.pdf --notes after_paragraph
refolio convert paper.pdf --markdown           # writes paper.md to edit by hand
refolio build paper.md --notes inline          # EPUB from an edited Markdown file
refolio convert scan.pdf --engine marker
```

## Development

```sh
git clone https://github.com/jeremiahcarey/refolio && cd refolio
uv run --python 3.12 --extra dev pytest
uv run refolio
```

- `tests/fixtures/make_fixtures.py` regenerates the test PDFs. It needs `pdflatex`, `pandoc` and LibreOffice. The PDFs are original filler text in three layouts:
  - a two-column journal page,
  - a Word export,
  - a book-style article with a JSTOR cover page.
- The EPUB tests run [EPUBCheck](https://www.w3.org/publishing/epubcheck/) when Java is available.

How it fits together:

| File | Role |
|---|---|
| `refolio/backends/builtin.py` | Text-layer converter: header/footer removal, XY-cut reading order, footnote, heading and quote detection |
| `refolio/backends/marker_backend.py` | Maps Marker's JSON blocks onto the same model |
| `refolio/assemble.py` | Links footnote marks to notes, merges split paragraphs, writes the Markdown |
| `refolio/epub.py` | Markdown → EPUB 3 in the four footnote modes |
| `refolio/cleanup.py` | The cleanup tools |
| `refolio/server.py`, `refolio/static/` | The local web app (FastAPI and plain JavaScript, no build step) |

Bug reports are most useful with the PDF attached, or at least a screenshot of the page that went wrong.

## License

Apache-2.0. See [LICENSE](LICENSE).
