import base64
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import wait_done
from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import JobManager, Limits, Tools, build_argv, build_env
from youtube_file_kit.procguard import RunResult, run_bounded

# One generated black frame and silent audio, with no device input or source media.
_MEDIA = base64.b64decode(
    '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'
    'IAcBGCAHARggBwEYIAcBGCAH'
)

URL = "https://www.youtube.com/watch?v=okokokokok1"
BASE = "http://127.0.0.1:8765"


def offline_tools(tmp_path):
    source = tmp_path / "fixture.mp4"
    source.write_bytes(_MEDIA)
    worker = Path(__file__).parent / "fakes" / "offline_ytdlp.py"
    return Tools(python=sys.executable, launcher=(sys.executable, "-I", str(worker), str(source)))


def test_pinned_cli_exits_101_after_success_when_max_downloads_is_one(tmp_path):
    tools = offline_tools(tmp_path)
    job = tmp_path / "legacy"
    for folder in ("home", "out", "tmp"):
        (job / folder).mkdir(parents=True)
    limits = Limits(max_bytes=100_000, timeout=15)
    argv = build_argv(URL, job, tools, limits)
    if "--max-downloads" not in argv:
        index = argv.index("--abort-on-error")
        argv[index:index] = ["--max-downloads", "1"]
    result = run_bounded(argv, cwd=str(job / "tmp"), env=build_env(job, tools), timeout=15)
    assert result.returncode == 101 and result.stopped is None
    assert (job / "out" / "media.mp4").read_bytes() == _MEDIA


def test_actual_cli_completion_is_served_as_the_original_fixture(tmp_path):
    tools = offline_tools(tmp_path)
    app = create_app(port=8765, tools=tools, limits=Limits(max_bytes=100_000, timeout=15),
                     root=tmp_path / "runtime")
    with TestClient(app, base_url=BASE) as client:
        client.headers.update({"Origin": BASE, "X-CSRF-Token": app.state.token})
        response = client.post("/api/jobs", json={"url": URL})
        assert response.status_code == 202
        job_id = response.json()["id"]
        result = wait_done(client, job_id)
        assert result == {"state": "done", "bytes": len(_MEDIA), "filename": "Offline fixture.mp4"}
        saved = client.get(f"/api/jobs/{job_id}/file")
        assert saved.status_code == 200 and saved.content == _MEDIA
        assert saved.headers["content-type"] == "video/mp4"


@pytest.mark.parametrize("returncode", [1, 2, 101])
@pytest.mark.parametrize("has_file", [False, True])
def test_nonzero_exit_never_publishes_even_a_valid_artifact(tmp_path, returncode, has_file):
    def failed(argv, *, cwd, **kwargs):
        if has_file:
            (Path(cwd).parent / "out" / "media.mp4").write_bytes(_MEDIA)
        return RunResult(returncode, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"), Limits(), runner=failed)
    try:
        job = manager.submit(URL)
        manager._pool.shutdown(wait=True)
        assert job.state == "error" and job.code == "failed"
        assert job.file_path is None and not job.dir.exists()
    finally:
        manager.shutdown()
