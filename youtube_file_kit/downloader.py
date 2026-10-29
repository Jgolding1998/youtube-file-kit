"""Bounded yt-dlp jobs: fixed argv, sanitized env, isolated job folders."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .names import safe_stem
from .procguard import run_bounded

OUTPUT_EXTS = {"mp4", "webm", "m4a", "mkv"}
META_PREFIX = b"YFKMETA "


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 2 * 1024**3
    max_duration: int = 3 * 3600
    timeout: float = 30 * 60
    max_concurrent: int = 2
    max_queued: int = 4
    job_ttl: float = 30 * 60
    max_kept: int = 8  # finished files kept for download; oldest go first
    memory_bytes: int = 2 * 1024**3


@dataclass(frozen=True)
class Tools:
    python: str
    ffmpeg: Optional[str] = None
    deno: Optional[str] = None
    # Internal override of the yt-dlp launcher, used by tests with a fake.
    launcher: tuple = ()


def find_tools(use_ffmpeg: bool = True, use_deno: bool = True) -> Tools:
    def which(name: str) -> Optional[str]:
        cwd = os.path.normcase(os.path.abspath(os.getcwd()))
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
            if not entry or not os.path.isabs(entry):
                continue
            directory = os.path.abspath(entry)
            if os.path.normcase(directory) == cwd:
                continue
            candidate = Path(directory) / (name + ".exe" if os.name == "nt" else name)
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
            except OSError:
                continue
        return None

    return Tools(
        python=sys.executable,
        ffmpeg=which("ffmpeg") if use_ffmpeg else None,
        deno=which("deno") if use_deno else None,
    )


def build_argv(url: str, job_dir: Path, tools: Tools, limits: Limits) -> list:
    """Full fixed argv. The only variable parts are app-controlled paths and the canonical URL."""
    launcher = list(tools.launcher) or [tools.python, "-I", "-X", "utf8", "-m", "yt_dlp"]
    argv = launcher + [
        "--ignore-config", "--no-config-locations", "--no-plugin-dirs",
        "--no-remote-components", "--no-js-runtimes",
        "--no-cookies", "--no-cookies-from-browser", "--no-cache-dir",
        "--no-batch-file", "--no-exec", "--no-update", "--no-playlist",
        "--use-extractors", "youtube",
        "--no-overwrites", "--no-continue", "--no-mtime", "--no-progress",
        "--abort-on-error",
        "--proxy", "", "--downloader", "native",
        "--socket-timeout", "20", "--retries", "3", "--fragment-retries", "3",
        "--max-filesize", str(limits.max_bytes),
        "--match-filters", f"!is_live & live_status!=is_upcoming & duration<={limits.max_duration}",
        "--paths", f"home:{job_dir / 'out'}", "--paths", f"temp:{job_dir / 'tmp'}",
        "--output", "media.%(ext)s",
        "--color", "no_color", "--quiet", "--no-warnings", "--no-simulate",
        "--print", "pre_process:YFKMETA %(.{id,title,duration,live_status})j",
    ]
    if tools.deno:
        argv += ["--js-runtimes", f"deno:{tools.deno}"]
    if tools.ffmpeg:
        argv += [
            "--ffmpeg-location", tools.ffmpeg, "--merge-output-format", "mp4",
            "--format", "bv*[height<=1080][ext=mp4][protocol=https]+ba[ext=m4a][protocol=https]"
                        "/b[ext=mp4][protocol=https]/b[protocol=https]",
        ]
    else:
        argv += ["--fixup", "never", "--format", "b[ext=mp4][protocol=https]/b[protocol=https]"]
    return argv + ["--", url]


def build_env(job_dir: Path, tools: Tools) -> dict:
    """Minimal child env: no inherited secrets, proxies or user config locations."""
    home = str(job_dir / "home")
    tmp = str(job_dir / "tmp")
    tool_dirs = [str(Path(p).parent) for p in (tools.ffmpeg, tools.deno) if p]
    env = {
        "PATH": os.pathsep.join(dict.fromkeys(tool_dirs)) or os.defpath,
        "HOME": home, "USERPROFILE": home, "APPDATA": home, "LOCALAPPDATA": home,
        "XDG_CONFIG_HOME": home, "XDG_CACHE_HOME": home, "XDG_DATA_HOME": home,
        "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
        "DENO_DIR": str(job_dir / "home" / "deno"), "DENO_NO_UPDATE_CHECK": "1", "DENO_NO_PROMPT": "1",
        "YTDLP_NO_PLUGINS": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1", "NO_COLOR": "1",
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    else:
        env["LANG"] = "C.UTF-8"
    return env


MESSAGES = {
    "invalid_url": "Paste one YouTube video link (youtube.com/watch, youtu.be or Shorts). Playlists and other sites are not supported.",
    "busy": "Too many downloads are running. Wait for one to finish and try again.",
    "private": "This video is private.",
    "members": "This video is only for channel members. This app does not sign in.",
    "age": "This video is age-restricted and needs a signed-in account. This app does not sign in.",
    "login": "YouTube requires sign-in for this video. This app does not sign in.",
    "region": "This video is not available in your region.",
    "drm": "This video is DRM protected and cannot be saved.",
    "blocked": "YouTube asked for a bot check. Try again later.",
    "rate_limited": "YouTube is limiting requests right now. Try again later.",
    "live": "Live streams and upcoming premieres are not supported.",
    "unavailable": "YouTube says this video is unavailable. It may be removed, private, or not offered to this connection.",
    "network": "Could not reach YouTube. Check your connection.",
    "too_long": "This video is longer than the app's length limit.",
    "too_large": "This video is larger than the app's size limit.",
    "no_format": "No downloadable format was found for this video.",
    "not_saved": "No file within the app's limits was saved for this video.",
    "timeout": "The download took too long and was stopped.",
    "cancelled": "The download was cancelled.",
    "failed": "The download failed.",
}

# Ordered (needle, code) pairs matched against lowercased yt-dlp stderr.
_PATTERNS = [
    ("drm protected", "drm"),
    ("private video", "private"),
    ("members-only", "members"), ("channel's members", "members"), ("join this channel", "members"),
    ("confirm your age", "age"), ("age-restricted", "age"), ("inappropriate for some users", "age"),
    ("available in your country", "region"), ("geo restriction", "region"),
    ("geo-restricted", "region"), ("not available from your location", "region"),
    ("not a bot", "blocked"), ("captcha", "blocked"),
    ("rate-limited", "rate_limited"), ("http error 429", "rate_limited"), ("too many requests", "rate_limited"),
    ("this live event", "live"), ("premieres in", "live"), ("is_upcoming", "live"),
    ("sign in", "login"), ("login required", "login"),
    ("larger than max-filesize", "too_large"),
    ("requested format is not available", "no_format"),
    ("video unavailable", "unavailable"), ("video is unavailable", "unavailable"), ("has been removed", "unavailable"),
    ("no longer available", "unavailable"), ("does not exist", "unavailable"),
    ("http error 404", "unavailable"), ("incomplete youtube id", "unavailable"),
    ("unable to download", "network"), ("getaddrinfo", "network"), ("name or service not known", "network"),
    ("timed out", "network"), ("connection", "network"), ("urlopen error", "network"),
]


def classify(stderr: bytes) -> str:
    text = stderr[-65536:].decode("utf-8", "replace").lower()
    for needle, code in _PATTERNS:
        if needle in text:
            return code
    return "failed"


def _dir_usage(path: Path) -> tuple[int, int]:
    total = 0
    largest = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            # os.stat, not the cached scandir entry: Windows does not refresh
                            # directory sizes for files that are still being written.
                            size = os.stat(entry.path, follow_symlinks=False).st_size
                            total += size
                            largest = max(largest, size)
                    except OSError:
                        continue
        except OSError:
            continue
    return total, largest


@dataclass
class Job:
    id: str
    url: str
    dir: Path
    state: str = "queued"  # queued, running, done, error
    code: Optional[str] = None
    bytes: int = 0
    filename: Optional[str] = None
    file_path: Optional[Path] = None
    finished: Optional[float] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    readers: int = field(default=0, repr=False)
    retired: bool = field(default=False, repr=False)
    future: Optional[Future] = field(default=None, repr=False)

    def public(self) -> dict:
        data = {"state": self.state, "bytes": self.bytes}
        if self.state == "done":
            data["filename"] = self.filename
        if self.code:
            data["error"] = self.code
            data["message"] = MESSAGES.get(self.code, MESSAGES["failed"])
        return data


class Busy(Exception):
    pass


@dataclass
class FileLease:
    path: Path
    filename: str
    _manager: JobManager = field(repr=False)
    _job: Job = field(repr=False)
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        self._manager._release_file(self)


class JobManager:
    def __init__(self, root: Path, tools: Tools, limits: Limits, runner: Callable = run_bounded):
        self.root, self.tools, self.limits, self.runner = Path(root), tools, limits, runner
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._stopped = False
        self._storage_slots = max(limits.max_kept + limits.max_concurrent,
                                  limits.max_concurrent + limits.max_queued)
        self._pool = ThreadPoolExecutor(max_workers=limits.max_concurrent, thread_name_prefix="yfk-job")

    def submit(self, url: str) -> Job:
        self.sweep()
        removed = []
        try:
            with self._lock:
                active = sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))
                if self._closed or active >= self.limits.max_concurrent + self.limits.max_queued:
                    raise Busy()
                removed.extend(self._take_excess_finished(1))
                charged = sum(j.state != "error" for j in self._jobs.values())
                needed = max(0, charged - self._storage_slots + 1)
                evictable = sorted((j for j in self._jobs.values() if j.state == "done" and not j.retired and not j.readers),
                                   key=lambda j: j.finished)
                if len(evictable) < needed:
                    raise Busy()
                removed.extend(j for j in evictable[:needed] if self._retire_locked(j))
                job_id = secrets.token_hex(16)
                job_dir = self.root / job_id
                for sub in ("out", "tmp", "home"):
                    (job_dir / sub).mkdir(parents=True, mode=0o700)
                job = Job(id=job_id, url=url, dir=job_dir)
                self._jobs[job_id] = job
                try:
                    job.future = self._pool.submit(self._run, job)
                except BaseException:
                    self._jobs.pop(job_id, None)
                    removed.append(job)
                    raise
        finally:
            for previous in removed:
                shutil.rmtree(previous.dir, ignore_errors=True)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job if job is not None and not job.retired else None

    def acquire_file(self, job_id: str) -> Optional[FileLease]:
        removed = None
        with self._lock:
            job = self._jobs.get(job_id)
            if self._closed or job is None or job.retired or job.state != "done" or job.file_path is None:
                return None
            if job.finished is not None and time.monotonic() - job.finished > self.limits.job_ttl:
                removed = job if self._retire_locked(job) else None
                lease = None
            elif not job.file_path.is_file():
                lease = None
            else:
                job.readers += 1
                lease = FileLease(job.file_path, job.filename or "youtube-video" + job.file_path.suffix, self, job)
        if removed is not None:
            shutil.rmtree(removed.dir, ignore_errors=True)
        return lease

    def _retire_locked(self, job: Job) -> bool:
        job.retired = True
        if job.readers:
            return False
        self._jobs.pop(job.id, None)
        return True

    def _release_file(self, lease: FileLease) -> None:
        with self._lock:
            if lease._released:
                return
            lease._released = True
            job = lease._job
            job.readers -= 1
            remove = job.retired and job.readers == 0
            if remove:
                self._jobs.pop(job.id, None)
            remove_root = self._stopped and not self._jobs
        if remove:
            shutil.rmtree(job.dir, ignore_errors=True)
        if remove_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.retired:
                return False
            job.cancel.set()
            future, job.future = job.future, None  # claim cleanup once for simultaneous cancels
        # Future callbacks run outside our lock. Cancellation only succeeds
        # before a worker claims the job; running work observes the event above.
        if future is not None and future.cancel():
            self._finish(job, "cancelled")
        return True

    def _finish(self, job: Job, code: Optional[str]) -> None:
        # Clean up first and publish the state last, so "error" always means "already removed".
        if code:
            job.file_path = None
            shutil.rmtree(job.dir, ignore_errors=True)
            job.code = code
        with self._lock:
            job.finished = time.monotonic()
            job.state = "error" if code else "done"
            extra = self._take_excess_finished(0) if not code else []
        for previous in extra:
            shutil.rmtree(previous.dir, ignore_errors=True)

    def _run(self, job: Job) -> None:
        try:
            if job.cancel.is_set():
                return self._finish(job, "cancelled")
            job.state = "running"
            self._finish(job, self._download(job))
        except Exception:
            self._finish(job, "failed")

    def _download(self, job: Job) -> Optional[str]:
        limits = self.limits
        meta: dict = {}
        pending: list = []
        last_check = [0.0]

        def on_line(line: bytes) -> None:
            if line.startswith(META_PREFIX):
                try:
                    data = json.loads(line[len(META_PREFIX):])
                except ValueError:
                    return
                if isinstance(data, dict):
                    meta.update(data)
                    duration = data.get("duration")
                    if data.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                        pending.append("live")
                    elif isinstance(duration, (int, float)) and duration > limits.max_duration:
                        pending.append("too_long")

        def watch() -> Optional[str]:
            if pending:
                return pending[0]
            now = time.monotonic()
            if now - last_check[0] >= 0.5:
                last_check[0] = now
                output, output_largest = _dir_usage(job.dir / "out")
                temporary, temporary_largest = _dir_usage(job.dir / "tmp")
                job.bytes = output + temporary
                # yt-dlp retains input streams while FFmpeg writes the merged file.
                # Reserve bounded working space without doubling the final-file cap.
                if (max(output_largest, temporary_largest) > limits.max_bytes
                        or job.bytes > 2 * limits.max_bytes):
                    return "too_large"
            return None

        result = self.runner(
            build_argv(job.url, job.dir, self.tools, limits),
            cwd=str(job.dir / "tmp"), env=build_env(job.dir, self.tools),
            # Windows Job Objects cap committed memory. A POSIX address-space rlimit would also
            # hit Deno (V8 reserves large virtual ranges), so there the cap is time and size only.
            timeout=limits.timeout, memory_bytes=limits.memory_bytes if os.name == "nt" else None,
            watch=watch, on_stdout_line=on_line, cancel=job.cancel,
        )
        if result.stopped:
            return result.stopped
        if pending:
            return pending[0]
        if result.returncode != 0:
            return classify(result.stderr)
        found = [p for p in (job.dir / "out").iterdir()
                 if p.is_file() and not p.is_symlink() and p.stem == "media"
                 and p.suffix[1:].lower() in OUTPUT_EXTS]
        if len(found) != 1:
            return classify(result.stderr) if result.stderr.strip() else "not_saved"
        path = found[0]
        size = path.stat().st_size
        if size == 0:
            return "not_saved"
        if size > limits.max_bytes:
            return "too_large"
        title = meta.get("title") if isinstance(meta.get("title"), str) else ""
        job.bytes = size
        job.file_path = path
        job.filename = f"{safe_stem(title, 'youtube-video')}{path.suffix.lower()}"
        return None

    def sweep(self) -> None:
        """Drop finished jobs past their TTL and delete their folders."""
        now = time.monotonic()
        with self._lock:
            expired = [j for j in self._jobs.values()
                       if j.finished is not None and now - j.finished > self.limits.job_ttl]
            removed = [job for job in expired if self._retire_locked(job)]
        for job in removed:
            shutil.rmtree(job.dir, ignore_errors=True)

    def _take_excess_finished(self, reserve: int) -> list[Job]:
        done = sorted((j for j in self._jobs.values() if j.state == "done" and not j.retired), key=lambda j: j.finished)
        extra = done[: max(0, len(done) - self.limits.max_kept + reserve)]
        return [job for job in extra if self._retire_locked(job)]

    def _trim_finished(self) -> None:
        """Bound disk use: keep at most max_kept finished downloads."""
        with self._lock:
            extra = self._take_excess_finished(1)
        for job in extra:
            shutil.rmtree(job.dir, ignore_errors=True)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._stopped = True
            removed = [job for job in list(self._jobs.values()) if self._retire_locked(job)]
            remove_root = not self._jobs
        for job in removed:
            shutil.rmtree(job.dir, ignore_errors=True)
        if remove_root:
            shutil.rmtree(self.root, ignore_errors=True)
