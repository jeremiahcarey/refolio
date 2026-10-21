"""Command line entry point.

    refolio                      open the app in your browser
    refolio convert paper.pdf    convert without the app (writes paper.epub)
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def serve(port: int = 8765, open_browser: bool = True):
    import uvicorn

    from .server import create_app

    port = _free_port(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Refolio is running at {url}\nLeave this window open while you use it; press Ctrl+C to quit.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


def convert_cmd(args):
    from .convert import convert
    from .epub import build_epub

    src = Path(args.pdf)
    result = convert(src, engine=args.engine, allow_ocr=not args.no_ocr,
                     progress=lambda f, m: print(f"  {m}…", file=sys.stderr))
    for w in result.warnings:
        print("note:", w, file=sys.stderr)
    if args.markdown:
        out = Path(args.output or src.with_suffix(".md"))
        out.write_text(result.markdown)
    else:
        out = Path(args.output or src.with_suffix(".epub"))
        r = build_epub(result.markdown, args.notes)
        out.write_bytes(r.data)
        for w in r.warnings:
            print("note:", w, file=sys.stderr)
    print(out)


def build_cmd(args):
    from .epub import build_epub

    src = Path(args.markdown)
    out = Path(args.output or src.with_suffix(".epub"))
    r = build_epub(src.read_text(), args.notes)
    out.write_bytes(r.data)
    for w in r.warnings:
        print("note:", w, file=sys.stderr)
    print(out)


def main(argv=None):
    from .epub import FOOTNOTE_MODES

    ap = argparse.ArgumentParser(prog="refolio", description="Turn PDFs of papers into EPUBs for listening.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("convert", help="convert a PDF to EPUB (or Markdown) without the app")
    c.add_argument("pdf")
    c.add_argument("-o", "--output")
    c.add_argument("--engine", choices=["builtin", "marker"], default="builtin")
    c.add_argument("--notes", choices=list(FOOTNOTE_MODES), default="endnotes",
                   help="how footnotes are handled (default: endnotes)")
    c.add_argument("--markdown", action="store_true", help="write the editable Markdown instead")
    c.add_argument("--no-ocr", action="store_true", help="Marker only: never OCR")

    b = sub.add_parser("build", help="build an EPUB from an edited Refolio Markdown file")
    b.add_argument("markdown")
    b.add_argument("-o", "--output")
    b.add_argument("--notes", choices=list(FOOTNOTE_MODES), default="endnotes")

    args = ap.parse_args(argv)
    if args.cmd == "convert":
        convert_cmd(args)
    elif args.cmd == "build":
        build_cmd(args)
    else:
        serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
