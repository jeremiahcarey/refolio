"""PDF -> editable Markdown, using one of the converters."""
from __future__ import annotations

from pathlib import Path

from .assemble import Assembled, assemble
from .backends import builtin, marker_backend

ENGINES = {
    "builtin": "Fast (built-in)",
    "marker": "Accurate (Marker)",
}


def available_engines() -> dict[str, bool]:
    return {"builtin": True, "marker": marker_backend.available()}


def _pdf_metadata(pdf_path) -> dict:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        meta = doc.get_metadata_dict()
        doc.close()
        return meta or {}
    except Exception:
        return {}


def convert(pdf_path: str | Path, engine: str = "builtin", allow_ocr: bool = True,
            progress=None) -> Assembled:
    if engine == "marker":
        if not marker_backend.available():
            raise RuntimeError(
                "Marker isn't installed. Install it with:  pip install \"refolio[marker]\"")
        ex = marker_backend.extract(pdf_path, allow_ocr=allow_ocr, progress=progress)
    else:
        ex = builtin.extract(pdf_path, progress=progress)

    meta = _pdf_metadata(pdf_path)
    title = (meta.get("Title") or "").strip()
    author = (meta.get("Author") or "").strip()
    # PDF metadata is often junk ("Microsoft Word - draft3.docx"); prefer what we found
    usable = (len(title) > 3 and not title.lower().startswith(("microsoft", "untitled"))
              and not title.lower().endswith((".doc", ".docx", ".pdf", ".tex", ".dvi")))
    if not ex.title and usable:
        ex.title = title
    if not ex.author and author and "@" not in author and len(author) < 120:
        ex.author = author
    if not ex.title:
        ex.title = Path(pdf_path).stem.replace("_", " ")
    if progress:
        progress(0.9, "Linking footnotes")
    return assemble(ex)
