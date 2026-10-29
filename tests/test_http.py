import json
import time

import pytest

from conftest import wait_done


def start(client, url):
    r = client.post("/api/jobs", json={"url": url})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_index_has_token_and_headers(make_client):
    client = make_client()
    r = client.get("/")
    assert r.status_code == 200
    assert client.app_state.token in r.text and "{{" not in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "server" not in r.headers
    assert "<script>" not in r.text and "http://" not in r.text.replace("https://github.com", "")


def test_success_download(make_client):
    client = make_client()
    job = start(client, "https://youtu.be/okokokokok1?si=abc")
    data = wait_done(client, job)
    assert data == {"state": "done", "bytes": 4012, "filename": "Test video.mp4"}
    r = client.get(f"/api/jobs/{job}/file")
    assert r.status_code == 200 and r.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"] == "attachment; filename*=utf-8''Test%20video.mp4"
    out = client.app_state.manager.get(job).dir / "out"
    record = json.loads((out / "record.json").read_text())
    assert record["argv"][-1] == "https://www.youtube.com/watch?v=okokokokok1"
    assert "YTDLP_NO_PLUGINS" in record["env"]
    assert record["cwd"].endswith("tmp")


def test_untrusted_title_becomes_safe_name(make_client):
    client = make_client()
    data = wait_done(client, start(client, "https://youtu.be/evilname001"))
    assert data["state"] == "done"
    assert data["filename"] == "CON _txt.exe.webm"


@pytest.mark.parametrize("vid, code", [
    ("privatevid1", "private"), ("agelimited1", "age"), ("regionlock1", "region"),
    ("drmvideo001", "drm"), ("membersonly", "members"), ("botcheck001", "blocked"),
    ("removed0001", "unavailable"), ("leaky000001", "failed"), ("nofile00001", "not_saved"),
    ("wrongext001", "not_saved"), ("toolong0001", "too_long"), ("livenow0001", "live"),
])
def test_failures_use_fixed_messages(make_client, vid, code):
    client = make_client()
    job = start(client, f"https://www.youtube.com/watch?v={vid}")
    started = time.monotonic()
    data = wait_done(client, job)
    assert data["state"] == "error" and data["error"] == code
    text = json.dumps(data)
    for leak in ("C:\\\\", "Users", "secret", "SECRET", "cookies", "ERROR", vid):
        assert leak not in text
    assert time.monotonic() - started < 15
    assert not client.app_state.manager.get(job).dir.exists()
    assert client.get(f"/api/jobs/{job}/file").status_code == 404


def test_size_limit_stops_download(make_client):
    client = make_client(max_bytes=200_000)
    data = wait_done(client, start(client, "https://youtu.be/bigfile0001"))
    assert data["error"] == "too_large"


def test_timeout(make_client):
    client = make_client(timeout=1)
    data = wait_done(client, start(client, "https://youtu.be/sleepy00001"))
    assert data["error"] == "timeout"


def test_cancel(make_client):
    client = make_client()
    job = start(client, "https://youtu.be/sleepy00001")
    time.sleep(0.5)
    assert client.post(f"/api/jobs/{job}/cancel", json={}).status_code == 200
    assert wait_done(client, job)["error"] == "cancelled"


def test_bounded_queue(make_client):
    client = make_client(max_concurrent=1, max_queued=1)
    a = start(client, "https://youtu.be/sleepy00001")
    b = start(client, "https://youtu.be/sleepy00001")
    r = client.post("/api/jobs", json={"url": "https://youtu.be/sleepy00001"})
    assert r.status_code == 429 and r.json()["error"] == "busy"
    for job in (a, b):
        client.post(f"/api/jobs/{job}/cancel", json={})
        wait_done(client, job)


@pytest.mark.parametrize("body", [
    {"url": "https://www.youtube.com/playlist?list=PL1"},
    {"url": "https://evil.example/watch?v=okokokokok1"},
    {"url": "file:///etc/passwd"},
    {"url": ["https://youtu.be/okokokokok1"]},
    {"url": "https://youtu.be/okokokokok1", "format": "bestvideo"},
    {"url": "https://youtu.be/okokokokok1", "exec": "calc"},
    {},
    [],
])
def test_bad_requests(make_client, body):
    client = make_client()
    r = client.post("/api/jobs", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_url"


def test_non_json_and_oversized(make_client):
    client = make_client()
    r = client.post("/api/jobs", content=b"url=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = client.post("/api/jobs", content=b"{", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = client.post("/api/jobs", content=b"x" * 5000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("headers, status", [
    ({"Host": "evil.example"}, 400),
    ({"Host": "127.0.0.1:9999"}, 400),
    ({"Host": "attacker.example:8765"}, 400),
    ({"Origin": "http://evil.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": ""}, 403),
    ({"X-CSRF-Token": "wrong"}, 403),
    ({"X-CSRF-Token": ""}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Sec-Fetch-Site": "same-site"}, 403),
])
def test_cross_origin_and_rebinding_rejected(make_client, headers, status):
    client = make_client()
    r = client.post("/api/jobs", json={"url": "https://youtu.be/okokokokok1"}, headers=headers)
    assert r.status_code == status
    assert client.app_state.manager.get(r.json().get("id", "x")) is None


def test_get_with_bad_host_rejected(make_client):
    client = make_client()
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_unknown_and_malformed_ids(make_client):
    client = make_client()
    for path in ("/api/jobs/" + "0" * 32, "/api/jobs/..%2F..%2Fx", "/api/jobs/" + "0" * 32 + "/file", "/nope"):
        assert client.get(path).status_code == 404


def test_shutdown_removes_runtime(make_client):
    client = make_client()
    root = client.app_state.manager.root
    wait_done(client, start(client, "https://youtu.be/okokokokok1"))
    assert any(root.iterdir())
    client.__exit__(None, None, None)
    assert not root.exists()
    client.__enter__()  # fixture teardown exits again
