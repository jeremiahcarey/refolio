"""Local web app: upload a PDF, review the Markdown next to the original pages, export EPUB.

Everything runs on 127.0.0.1; nothing is uploaded anywhere.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, cleanup
from .convert import ENGINES, available_engines, convert
from .epub import FOOTNOTE_MODES, build_epub, parse_front_matter

STATIC = Path(__file__).parent / "static"
_pdf_lock = threading.Lock()          # pdfium is not thread-safe
_executor = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict] = {}


def data_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "Refolio"
    old = Path.home() / "Library" / "Application Support" / "Earmark"  # the app's former name
    if not (Path.home() / "Library").exists():  # not macOS
        d = Path.home() / ".refolio"
        old = Path.home() / ".earmark"
    if old.exists() and not d.exists():
        old.rename(d)
    (d / "projects").mkdir(parents=True, exist_ok=True)
    return d


def _proj(pid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise HTTPException(404, "No such document")
    p = data_dir() / "projects" / pid
    if not p.exists():
        raise HTTPException(404, "No such document")
    return p


def _read_meta(p: Path) -> dict:
    try:
        return json.loads((p / "meta.json").read_text())
    except Exception:
        return {}


def _write_meta(p: Path, meta: dict):
    (p / "meta.json").write_text(json.dumps(meta, indent=1))


def _run_conversion(pid: str, engine: str, allow_ocr: bool):
    p = data_dir() / "projects" / pid
    job = _jobs[pid]

    def progress(frac, msg):
        job.update(progress=frac, message=msg)

    try:
        lock = _pdf_lock if engine == "builtin" else threading.Lock()
        with lock:
            result = convert(p / "source.pdf", engine=engine, allow_ocr=allow_ocr, progress=progress)
        (p / "document.md").write_text(result.markdown)
        meta = _read_meta(p)
        fm, _ = parse_front_matter(result.markdown)
        meta.update(title=fm.get("title", ""), engine=engine, warnings=result.warnings, stats=result.stats,
                    converted=time.time(), updated=time.time())
        _write_meta(p, meta)
        job.update(status="done", progress=1.0, message="Done")
    except Exception as e:  # report to the UI
        job.update(status="error", message=str(e) or e.__class__.__name__)


def _page_count(pdf: Path) -> int:
    import pypdfium2 as pdfium
    with _pdf_lock:
        doc = pdfium.PdfDocument(str(pdf))
        n = len(doc)
        doc.close()
    return n


class MarkdownBody(BaseModel):
    markdown: str


class CleanupBody(BaseModel):
    op: str
    markdown: str


class PreviewBody(BaseModel):
    markdown: str
    mode: str = "endnotes"


class ExportBody(BaseModel):
    markdown: str
    mode: str = "endnotes"
    title: str | None = None
    author: str | None = None


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def create_app() -> FastAPI:
    app = FastAPI(title="Refolio", version=__version__)

    @app.middleware("http")
    async def local_requests_only(request: Request, call_next):
        # A web page open in the same browser can send requests to 127.0.0.1. The Host
        # check stops DNS rebinding; the Origin check stops cross-site commands.
        host = request.headers.get("host", "")
        if host.rsplit(":", 1)[0] not in LOCAL_HOSTS and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Unexpected host."}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != host:
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info():
        avail = available_engines()
        return {
            "version": __version__,
            "engines": [{"id": k, "label": v, "available": avail[k]} for k, v in ENGINES.items()],
            "footnote_modes": [{"id": k, "label": v} for k, v in FOOTNOTE_MODES.items()],
            "cleanup": [{"id": k, "label": v[0], "short": v[1], "group": v[2]}
                        for k, v in cleanup.OPERATIONS.items()],
        }

    @app.get("/api/projects")
    def list_projects():
        items = []
        for p in (data_dir() / "projects").iterdir():
            meta = _read_meta(p)
            if meta:
                items.append({"id": p.name, "name": meta.get("title") or meta.get("name"), "updated": meta.get("updated", 0),
                              "pages": meta.get("pages")})
        items.sort(key=lambda m: m["updated"], reverse=True)
        return items[:30]

    @app.post("/api/projects")
    async def upload(file: UploadFile = File(...), engine: str = Form("builtin"),
                     allow_ocr: bool = Form(True)):
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "That file isn't a PDF.")
        if engine not in ENGINES:
            raise HTTPException(400, "Unknown converter.")
        pid = uuid.uuid4().hex[:12]
        p = data_dir() / "projects" / pid
        p.mkdir(parents=True)
        (p / "source.pdf").write_bytes(data)
        try:
            pages = _page_count(p / "source.pdf")
        except Exception:
            shutil.rmtree(p, ignore_errors=True)
            raise HTTPException(400, "This PDF can't be opened. It may be damaged or password-protected.")
        _write_meta(p, {"name": file.filename or "document.pdf", "pages": pages,
                        "created": time.time(), "updated": time.time()})
        _jobs[pid] = {"status": "working", "progress": 0.02, "message": "Starting"}
        _executor.submit(_run_conversion, pid, engine, allow_ocr)
        return {"id": pid}

    @app.post("/api/projects/{pid}/reconvert")
    def reconvert(pid: str, engine: str = Form("builtin"), allow_ocr: bool = Form(True)):
        _proj(pid)
        if engine not in ENGINES:
            raise HTTPException(400, "Unknown converter.")
        _jobs[pid] = {"status": "working", "progress": 0.02, "message": "Starting"}
        _executor.submit(_run_conversion, pid, engine, allow_ocr)
        return {"id": pid}

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        p = _proj(pid)
        meta = _read_meta(p)
        job = _jobs.get(pid)
        out = {"id": pid, **meta}
        if job and job["status"] != "done":
            out.update(status=job["status"], progress=job["progress"], message=job["message"])
            return out
        md_path = p / "document.md"
        if not md_path.exists():
            out.update(status="error", message="This document was never converted. Try converting again.")
            return out
        md = md_path.read_text()
        fm, _ = parse_front_matter(md)
        out.update(status="done", markdown=md, title=fm.get("title", ""), author=fm.get("author", ""))
        return out

    @app.put("/api/projects/{pid}/markdown")
    def save(pid: str, body: MarkdownBody):
        p = _proj(pid)
        (p / "document.md").write_text(body.markdown)
        meta = _read_meta(p)
        meta["updated"] = time.time()
        _write_meta(p, meta)
        return {"ok": True}

    @app.delete("/api/projects/{pid}")
    def delete(pid: str):
        p = _proj(pid)
        shutil.rmtree(p, ignore_errors=True)
        _jobs.pop(pid, None)
        return {"ok": True}

    @app.get("/api/projects/{pid}/pages/{n}.png")
    def page_image(pid: str, n: int, w: int = 1000):
        p = _proj(pid)
        w = max(300, min(w, 2000))
        cache = p / "pages" / f"{n}-{w}.png"
        if not cache.exists():
            import pypdfium2 as pdfium
            with _pdf_lock:
                doc = pdfium.PdfDocument(str(p / "source.pdf"))
                if not 1 <= n <= len(doc):
                    doc.close()
                    raise HTTPException(404, "No such page")
                page = doc[n - 1]
                scale = w / page.get_width()
                img = page.render(scale=scale).to_pil()
                doc.close()
            cache.parent.mkdir(exist_ok=True)
            buf = BytesIO()
            img.save(buf, "PNG", optimize=True)
            cache.write_bytes(buf.getvalue())
        return FileResponse(cache, media_type="image/png",
                            headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/projects/{pid}/source.pdf")
    def source(pid: str):
        p = _proj(pid)
        return FileResponse(p / "source.pdf", media_type="application/pdf")

    @app.post("/api/cleanup")
    def run_cleanup(body: CleanupBody):
        try:
            md, n = cleanup.run(body.op, body.markdown)
        except KeyError:
            raise HTTPException(400, "Unknown cleanup step.")
        return {"markdown": md, "count": n}

    @app.post("/api/preview")
    def preview(body: PreviewBody):
        r = build_epub(body.markdown, body.mode)
        return {"html": r.body_html, "warnings": r.warnings}

    @app.post("/api/projects/{pid}/export")
    def export(pid: str, body: ExportBody):
        p = _proj(pid)
        (p / "document.md").write_text(body.markdown)
        r = build_epub(body.markdown, body.mode, title=body.title, author=body.author)
        name = (body.title or _read_meta(p).get("name") or "document").strip()
        name = re.sub(r"[^\w\s\-.,'()]+", "", name).strip()[:90] or "document"
        name = re.sub(r"\.pdf$", "", name, flags=re.I)
        from urllib.parse import quote
        return Response(r.data, media_type="application/epub+zip", headers={
            "Content-Disposition": f"attachment; filename=\"{name}.epub\"; filename*=UTF-8''{quote(name)}.epub",
            "X-Refolio-Warnings": quote(json.dumps(r.warnings)),
        })

    @app.exception_handler(Exception)
    async def errors(request, exc):
        return JSONResponse({"detail": f"Something went wrong: {exc}"}, status_code=500)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
