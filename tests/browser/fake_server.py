"""Serve the real app with the fake yt-dlp transport, for browser tests only."""

import sys
import threading
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from youtube_file_kit.app import create_app  # noqa: E402
from youtube_file_kit.downloader import Limits, Tools  # noqa: E402

port = int(sys.argv[1])
fake = ROOT / "tests" / "fakes" / "fake_ytdlp.py"
tools = Tools(python=sys.executable, launcher=(sys.executable, "-I", str(fake)))
app = create_app(port=port, tools=tools, limits=Limits(timeout=20), root=Path(sys.argv[2]))
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                                     lifespan="on", server_header=False, proxy_headers=False, ws="none"))


def stop_from_parent():
    sys.stdin.readline()
    server.should_exit = True


threading.Thread(target=stop_from_parent, daemon=True).start()
server.run()
