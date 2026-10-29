import threading

import pytest

from youtube_file_kit.downloader import JobManager, Limits, Tools
from youtube_file_kit.procguard import RunResult

BASE = "http://127.0.0.1:8765"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("origin", ["null", "http://outside.invalid", BASE + "/", "http://localhost:8765"])
def test_read_requests_reject_present_foreign_origins(make_client, method, origin):
    client = make_client()
    response = client.request(method, "/", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"


def test_navigation_without_origin_and_same_origin_reads_still_work(make_client):
    client = make_client()
    assert client.get("/").status_code == 200
    client.headers.pop("Origin")
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200


@pytest.mark.parametrize("name,value", [
    ("Origin", BASE), ("Content-Length", "0"), ("Content-Type", "application/json"),
])
def test_duplicate_security_headers_are_rejected_on_reads(make_client, name, value):
    client = make_client()
    response = client.get("/", headers=[(name, value), (name, value)])
    assert response.status_code == 400


@pytest.mark.parametrize("field", ['"url"', '"u\\u0072l"'])
def test_duplicate_decoded_json_keys_never_start_a_download(make_client, field):
    client = make_client()
    body = '{"url":"not a link",' + field + ':"https://youtu.be/okokokokok1"}'
    response = client.post("/api/jobs", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_url"
    assert not client.app_state.manager._jobs


def test_simultaneous_completions_respect_the_finished_file_cap(tmp_path):
    barrier = threading.Barrier(6)

    def complete(argv, *, cwd, **kwargs):
        from pathlib import Path
        output = Path(cwd).parent / "out" / "media.mp4"
        output.write_bytes(b"synthetic media fixture")
        barrier.wait(timeout=5)
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"),
                         Limits(max_concurrent=6, max_queued=0, max_kept=2), runner=complete)
    try:
        for _ in range(6):
            manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        manager._pool.shutdown(wait=True)
        assert sum(job.state == "done" for job in manager._jobs.values()) == 2
        assert len(list(manager.root.iterdir())) == 2
    finally:
        manager.shutdown()


def test_pinned_deno_solver_uses_no_runtime_permissions():
    from types import SimpleNamespace
    from yt_dlp.extractor.youtube.jsc._builtin.deno import DenoJCP

    provider = DenoJCP.__new__(DenoJCP)
    provider._lib_script = SimpleNamespace(variant=object())
    provider.ie = SimpleNamespace(get_param=lambda name: False)
    provider.ejs_setting = lambda name, default: default
    provider._run_deno = lambda text, options: options
    options = provider._run_js_runtime("null")
    assert {"--no-prompt", "--no-remote", "--no-npm", "--cached-only", "--no-config"} <= set(options)
    assert not any(value.startswith("--allow-") for value in options)
