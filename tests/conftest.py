import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import Limits, Tools

FAKE = Path(__file__).parent / "fakes" / "fake_ytdlp.py"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def fake_tools():
    return Tools(python=sys.executable, launcher=(sys.executable, "-I", str(FAKE)))


@pytest.fixture
def make_client(tmp_path, fake_tools):
    clients = []

    def make(**limit_overrides):
        limits = Limits(**{"timeout": 20, **limit_overrides})
        app = create_app(port=PORT, tools=fake_tools, limits=limits, root=tmp_path / "runtime")
        client = TestClient(app, base_url=BASE)
        client.__enter__()
        client.headers.update({"Origin": BASE, "X-CSRF-Token": app.state.token})
        client.app_state = app.state
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def wait_done(client, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["state"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish")
