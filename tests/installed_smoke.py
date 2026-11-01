"""Run only from the isolated installed-wheel check, with fake processing."""
import hashlib
from importlib import import_module, resources
from importlib.metadata import distribution
import io
import json
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
import zipfile

SOURCE = Path(sys.argv[1]).resolve()
CHECKS = []


def check(condition, name):
    if not condition:
        raise AssertionError(name)
    CHECKS.append(name)


def installed(package, assets):
    module = import_module(package)
    location = Path(module.__file__).resolve()
    check(location.is_relative_to(Path(sys.prefix).resolve()) and not location.is_relative_to(SOURCE),
          "imports from installed site-packages outside checkout")
    files = resources.files(package)
    metadata = distribution(package.replace("_", "-"))
    dependencies = {item.split("==", 1)[0] for item in metadata.requires or []}
    check(dependencies == {"yt-dlp", "yt-dlp-ejs", "starlette", "uvicorn"}, "installed wheel declares every direct runtime dependency")
    hashes = {}
    for asset in assets:
        data = files.joinpath(asset).read_bytes()
        check(bool(data), "packaged resource " + asset)
        hashes[asset] = hashlib.sha256(data).hexdigest()
    return hashes


def headers(response):
    check(response.headers.get("cache-control") == "no-store", "responses are not cached")
    check("frame-ancestors 'none'" in response.headers.get("content-security-policy", ""),
          "frame protection retained")


from starlette.testclient import TestClient
from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import Limits, Tools
from youtube_file_kit.procguard import RunResult

hashes = installed("youtube_file_kit", ("static/index.html", "static/app.js", "static/style.css"))
calls = []
output = b"OFFLINE-DOWNLOADER-FIXTURE"


def fake_runner(argv, *, cwd, on_stdout_line, **kwargs):
    calls.append(argv)
    check(argv[-2:] == ["--", "https://www.youtube.com/watch?v=okokokokok1"], "canonical fixed URL argv")
    on_stdout_line(b'YFKMETA {"id":"okokokokok1","title":"Synthetic fixture","duration":1,"live_status":"not_live"}')
    (Path(cwd).parent / "out" / "media.mp4").write_bytes(output)
    return RunResult(0, b"", b"")


app = create_app(port=18765, tools=Tools(python="never-execute"), runner=fake_runner,
                 limits=Limits(timeout=5, max_bytes=4096), root=Path.cwd() / "synthetic data 空间")
with TestClient(app, base_url="http://127.0.0.1:18765") as client:
    page = client.get("/")
    check(page.status_code == 200, "installed homepage works")
    headers(page)
    check(client.get("/app.js").status_code == client.get("/style.css").status_code == 200,
          "installed static routes work")
    check(client.get("/", headers={"Host": "foreign.invalid"}).status_code == 400, "foreign host refused")
    check(client.post("/api/jobs", json={"url": "https://youtu.be/okokokokok1"}).status_code == 403 and calls == [],
          "unauthorized job refuses before downloader")
    client.headers.update({"Origin": "http://127.0.0.1:18765", "X-CSRF-Token": app.state.token})
    response = client.post("/api/jobs", json={"url": "https://youtu.be/okokokokok1"})
    check(response.status_code == 202, "installed fake job accepted")
    job_id = response.json()["id"]
    deadline = time.monotonic() + 5
    while True:
        job = client.get("/api/jobs/" + job_id).json()
        if job["state"] in {"done", "error"} or time.monotonic() >= deadline:
            break
        time.sleep(.01)
    check(job["state"] == "done" and len(calls) == 1, "fake downloader completes without traffic")
    check(client.get("/api/jobs/" + job_id + "/file").content == output, "leased download retains exact fake bytes")

print(json.dumps({"installed": True, "checks": CHECKS, "resources_sha256": hashes,
                  "native_tools_executed": False, "network_requests": False}))

