"""Loopback web app: one YouTube link in, one saved video file out."""

from __future__ import annotations

import contextlib
import json
import re
import secrets
from importlib import resources
from pathlib import Path

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from .downloader import MESSAGES, Busy, JobManager, Limits, Tools, find_tools
from . import runtime
from .guard import BodyTooLarge, LocalGuard
from .urls import InvalidURL, canonical_video_url

_JOB_ID = re.compile(r"[0-9a-f]{32}")
_MEDIA_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".m4a": "audio/mp4", ".mkv": "video/x-matroska"}
_STATIC = {"/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}


def _size_text(nbytes: int) -> str:
    gib = nbytes / 1024**3
    return f"{gib:g} GB" if gib >= 1 else f"{nbytes // (1024 * 1024)} MB"


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": MESSAGES.get(code, MESSAGES["failed"])}, status_code=status)


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Repeated field.")
        result[name] = value
    return result


def create_app(*, port: int, tools: Tools | None = None, limits: Limits | None = None,
               root: Path | None = None, runner=None) -> LocalGuard:
    token = secrets.token_urlsafe(32)
    limits = limits or Limits()
    tools = tools or find_tools()
    owned = root is None
    root = runtime.create("youtube-file-kit-") if owned else Path(root)
    kwargs = {"runner": runner} if runner else {}
    manager = JobManager(root, tools, limits, **kwargs)
    static_dir = resources.files(__package__) / "static"
    limits_line = (f"Public videos only. One link at a time, up to {limits.max_duration // 60} minutes "
                   f"or {_size_text(limits.max_bytes)}.")
    index_html = ((static_dir / "index.html").read_text("utf-8")
                  .replace("{{CSRF}}", token).replace("{{LIMITS}}", limits_line))
    static_files = {path: ((static_dir / name).read_bytes(), media) for path, (name, media) in _STATIC.items()}

    async def index(request: Request) -> Response:
        return Response(index_html, media_type="text/html; charset=utf-8")

    async def static(request: Request) -> Response:
        body, media = static_files[request.url.path]
        return Response(body, media_type=media)

    async def favicon(request: Request) -> Response:
        return Response(status_code=204)

    async def create_job(request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return _error(415, "invalid_url")
        try:
            data = json.loads(await request.body(), object_pairs_hook=_unique_fields)
        except (BodyTooLarge, ValueError, RecursionError):
            return _error(400, "invalid_url")
        if not isinstance(data, dict) or set(data) != {"url"}:
            return _error(400, "invalid_url")
        try:
            url = canonical_video_url(data["url"])
        except InvalidURL:
            return _error(400, "invalid_url")
        try:
            job = manager.submit(url)
        except Busy:
            return _error(429, "busy")
        return JSONResponse({"id": job.id}, status_code=202)

    def lookup(request: Request):
        job_id = request.path_params["job_id"]
        return manager.get(job_id) if _JOB_ID.fullmatch(job_id) else None

    async def job_status(request: Request) -> Response:
        job = lookup(request)
        if job is None:
            return JSONResponse({"error": "not_found", "message": "Unknown download."}, status_code=404)
        return JSONResponse(job.public())

    async def job_cancel(request: Request) -> Response:
        job = lookup(request)
        if job is None:
            return JSONResponse({"error": "not_found", "message": "Unknown download."}, status_code=404)
        manager.cancel(job.id)
        return JSONResponse({"ok": True})

    async def job_file(request: Request) -> Response:
        job = lookup(request)
        if job is None or job.state != "done" or job.file_path is None or not job.file_path.is_file():
            return JSONResponse({"error": "not_found", "message": "File is not ready or has expired."},
                                status_code=404)
        media = _MEDIA_TYPES.get(job.file_path.suffix.lower(), "application/octet-stream")
        return FileResponse(job.file_path, filename=job.filename, media_type=media)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def sweeper():
            while True:
                await anyio.sleep(60)
                await anyio.to_thread.run_sync(manager.sweep)
                if owned:
                    runtime.touch(root)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(sweeper)
                try:
                    yield
                finally:
                    tg.cancel_scope.cancel()
        finally:
            # Outside the cancelled scope, so cleanup really runs.
            await anyio.to_thread.run_sync(manager.shutdown)

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/app.js", static, methods=["GET"]),
        Route("/style.css", static, methods=["GET"]),
        Route("/favicon.ico", favicon, methods=["GET"]),
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/jobs/{job_id}", job_status, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", job_cancel, methods=["POST"]),
        Route("/api/jobs/{job_id}/file", job_file, methods=["GET"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.manager = manager
    app.state.token = token
    guarded = LocalGuard(app, port=port, token=token, body_limit=lambda path: 4096)
    guarded.state = app.state
    return guarded
