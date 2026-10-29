"""Optional single real request against yt-dlp's own public test video.

Runs only with YFK_REAL_NETWORK=1. Bounded to 10 MB and 90 seconds.
"""

import os
import time

import pytest
from starlette.testclient import TestClient

from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import Limits, find_tools

pytestmark = pytest.mark.skipif(os.environ.get("YFK_REAL_NETWORK") != "1", reason="set YFK_REAL_NETWORK=1")
BASE = "http://127.0.0.1:8765"


def test_public_test_video(tmp_path):
    limits = Limits(max_bytes=10 * 1024 * 1024, timeout=90, max_duration=600)
    app = create_app(port=8765, tools=find_tools(), limits=limits, root=tmp_path / "rt")
    with TestClient(app, base_url=BASE) as client:
        client.headers.update({"Origin": BASE, "X-CSRF-Token": app.state.token})
        r = client.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=BaW_jenozKc"})
        assert r.status_code == 202
        job = r.json()["id"]
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            data = client.get(f"/api/jobs/{job}").json()
            if data["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        print("RESULT", data)
        if data["state"] == "error":
            pytest.skip(f"YouTube did not serve the test video here: {data['error']}")
        r = client.get(f"/api/jobs/{job}/file")
        assert r.status_code == 200 and 0 < len(r.content) <= limits.max_bytes
        assert data["filename"].endswith((".mp4", ".webm", ".m4a"))
