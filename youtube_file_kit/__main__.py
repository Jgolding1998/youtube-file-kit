"""Start the local app: python -m youtube_file_kit"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .app import create_app
from .downloader import Limits, find_tools


def main() -> None:
    parser = argparse.ArgumentParser(prog="youtube_file_kit", description="Local YouTube file saver.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--no-deno", action="store_true", help="do not let yt-dlp use Deno")
    parser.add_argument("--no-ffmpeg", action="store_true", help="single-file formats only")
    parser.add_argument("--max-minutes", type=int, default=180, help="longest video allowed")
    parser.add_argument("--max-mb", type=int, default=2048, help="largest file allowed")
    args = parser.parse_args()
    if not (1024 <= args.port <= 65535 and 1 <= args.max_minutes <= 600 and 1 <= args.max_mb <= 16384):
        parser.error("value out of range")

    tools = find_tools(use_ffmpeg=not args.no_ffmpeg, use_deno=not args.no_deno)
    limits = Limits(max_bytes=args.max_mb * 1024 * 1024, max_duration=args.max_minutes * 60)
    app = create_app(port=args.port, tools=tools, limits=limits)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"YouTube File Kit running at {url}  (Ctrl+C to stop)")
    print(f"ffmpeg: {'found' if tools.ffmpeg else 'not found'}  deno: {'found' if tools.deno else 'not found'}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False,
        server_header=False, proxy_headers=False, ws="none", http="h11", lifespan="on",
        limit_concurrency=64, timeout_keep_alive=5, timeout_graceful_shutdown=5,
        h11_max_incomplete_event_size=16384,
    )


if __name__ == "__main__":
    main()
