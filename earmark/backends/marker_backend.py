"""Converter that uses Marker (https://github.com/datalab-to/marker).

Marker runs layout-detection models, so it copes with messier PDFs than the
built-in converter: unusual layouts, scanned pages (OCR), tables. It is optional:
install with ``pip install "earmark[marker]"``. The first run downloads its models.

Marker's code is Apache-2.0. Its model weights are free for research, personal use
and small organisations; see Marker's README for the commercial terms.
"""
from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser

from ..model import Block, Extracted
from ..textutil import escape_block_start, escape_inline, normalize_spaces

_models = None


def available() -> bool:
    try:
        import marker  # noqa: F401
        return True
    except Exception:
        return False


class _Inline(HTMLParser):
    """Marker block HTML -> inline Markdown (keeps <sup> for footnote marks)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.just_opened = False

    def handle_starttag(self, tag, attrs):
        self.just_opened = tag in ("i", "em", "b", "strong")
        if tag in ("i", "em"):
            self.out.append("*")
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag == "sup":
            self.out.append("<sup>")
        elif tag == "br":
            self.out.append(" ")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag in ("script", "style"):
            self.skip += 1

    def _close(self, mark):
        # move trailing whitespace outside the emphasis: "*word *" -> "*word* "
        trail = ""
        while self.out and self.out[-1] != self.out[-1].rstrip():
            last = self.out.pop()
            trail = last[len(last.rstrip()):] + trail
            if last.rstrip():
                self.out.append(last.rstrip())
                break
        self.out.append(mark + trail)

    def handle_endtag(self, tag):
        self.just_opened = False
        if tag in ("i", "em"):
            self._close("*")
        elif tag in ("b", "strong"):
            self._close("**")
        elif tag == "sup":
            self.out.append("</sup>")
        elif tag in ("tr", "p", "li"):
            self.out.append(" ")
        elif tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            return
        data = escape_inline(data)
        # move leading whitespace outside an emphasis that was just opened
        if self.just_opened and self.out and self.out[-1] in ("*", "**") and data[:1].isspace():
            mark = self.out.pop()
            self.out.append(" " + mark)
            data = data.lstrip()
        self.just_opened = False
        self.out.append(data)


def html_to_inline(html: str) -> str:
    p = _Inline()
    p.feed(html or "")
    text = "".join(p.out)
    text = re.sub(r"\*\*\*\*|(?<![*\w])\*\*(?![*\w])", "", text)  # empty emphasis
    return normalize_spaces(htmllib.unescape(text))


_KIND = {
    "Text": "para", "TextInlineMath": "para", "Code": "para", "Equation": "para",
    "Handwriting": "para", "Form": "para", "ComplexRegion": "para", "Bibliography": "para",
    "TableOfContents": "para", "Table": "para", "ListItem": "list", "Caption": "caption",
    "Footnote": "footnote", "SectionHeader": "heading",
}
_SKIP = {"PageHeader", "PageFooter", "Picture", "Figure", "Diagram", "ChemicalBlock",
         "Reference", "Line", "Span", "Char"}


def _walk(node, page_no, out, warnings):
    bt = node.block_type
    if bt in _SKIP:
        if bt in ("Picture", "Figure", "Diagram"):
            warnings.append(f"Skipped an image on page {page_no}.")
        return
    if node.children and bt not in _KIND:
        for c in node.children:
            _walk(c, page_no, out, warnings)
        return
    kind = _KIND.get(bt, "para")
    text = html_to_inline(node.html)
    if not text:
        return
    level = 0
    if kind == "heading":
        m = re.search(r"<h([1-6])", node.html or "")
        level = int(m.group(1)) if m else 2
        text = text.replace("**", "")
    else:
        text = escape_block_start(text)
    out.append(Block(kind, text, page_no, level, tuple(node.bbox) if node.bbox else None))


def extract(pdf_path, allow_ocr: bool = True, progress=None) -> Extracted:
    global _models
    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    if progress:
        progress(0.1, "Loading Marker models (the first run downloads them)")
    if _models is None:
        _models = create_model_dict()
    options = {"output_format": "json", "disable_image_extraction": True}
    warnings: list[str] = []
    # Marker's OCR model runs in llama.cpp's llama-server on a Mac. Without it, use the
    # PDF's text layer only, rather than failing halfway through.
    import os
    import shutil
    if allow_ocr and not shutil.which(os.environ.get("LLAMA_CPP_BINARY") or "llama-server") \
            and not os.environ.get("SURYA_INFERENCE_URL"):
        allow_ocr = False
        warnings.append("OCR was off because llama.cpp isn't installed (on a Mac: brew install llama.cpp). "
                        "Scanned pages will come out empty.")
    if not allow_ocr:
        options["disable_ocr"] = True
    cp = ConfigParser(options)
    converter = PdfConverter(
        config=cp.generate_config_dict(),
        artifact_dict=_models,
        processor_list=cp.get_processors(),
        renderer=cp.get_renderer(),
    )
    if progress:
        progress(0.3, "Marker is analysing the layout")
    rendered = converter(str(pdf_path))
    blocks: list[Block] = []
    images: list[str] = []
    for i, page in enumerate(rendered.children):
        _walk(page, i + 1, blocks, images)
    if images:
        warnings.append(f"Skipped {len(images)} image(s) (figures aren't read aloud).")

    # heading levels: first heading on the first pages becomes the title (level 1)
    title = ""
    heads = [b for b in blocks if b.kind == "heading"]
    if heads:
        top = min(h.level for h in heads)
        first = heads[0]
        if first.page <= 2:
            title = re.sub(r"<sup>.*?</sup>", "", first.text).strip()
        for h in heads:
            h.level = 1 if (h is first and title) else max(2, h.level - top + 2)
    return Extracted(blocks, len(rendered.children), title=title, warnings=warnings)
