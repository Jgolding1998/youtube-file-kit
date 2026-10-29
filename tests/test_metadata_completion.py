import json
from pathlib import Path

import pytest

from youtube_file_kit.downloader import JobManager, Limits, Tools
from youtube_file_kit.procguard import RunResult


@pytest.mark.parametrize("meta,has_file,code", [
    ({"duration": 99, "live_status": "not_live"}, False, "too_long"),
    ({"duration": 5, "live_status": "post_live"}, True, "live"),
])
def test_metadata_verdict_survives_exit_between_watcher_polls(tmp_path, meta, has_file, code):
    def fast(argv, *, cwd, on_stdout_line, **kwargs):
        on_stdout_line(b"YFKMETA " + json.dumps({"title": "synthetic video", **meta}).encode())
        if has_file:
            (Path(cwd).parent / "out" / "media.mp4").write_bytes(b"synthetic media")
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"), Limits(max_duration=10), runner=fast)
    try:
        job = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        manager._pool.shutdown(wait=True)
        assert (job.state, job.code) == ("error", code)
        assert job.file_path is None and not job.dir.exists()
    finally:
        manager.shutdown()
