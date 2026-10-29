from pathlib import Path

import pytest

from youtube_file_kit.downloader import JobManager, Limits, Tools
from youtube_file_kit.procguard import RunResult


@pytest.mark.parametrize("parts,merged,expected", [
    ((600, 300), 900, None),  # Inputs remain beside the valid merge output.
    ((1001,), 100, "too_large"),  # Individual unfinished files still have the file cap.
    ((800, 800, 800), 100, "too_large"),  # Aggregate temporary storage stays bounded.
])
def test_merge_working_budget_and_file_caps(tmp_path, parts, merged, expected):
    observations = []

    def merge(argv, *, cwd, watch, **kwargs):
        temp = Path(cwd)
        for index, size in enumerate(parts):
            (temp / f"media.f{index}.part").write_bytes(b"x" * size)
        target = temp / "media.temp.mp4"
        target.write_bytes(b"x" * merged)
        stopped = watch()
        observations.append(stopped)
        if stopped:
            return RunResult(0, b"", b"", stopped=stopped)
        for part in temp.glob("*.part"):
            part.unlink()
        target.replace(temp.parent / "out" / "media.mp4")
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"), Limits(max_bytes=1000), runner=merge)
    try:
        job = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        manager._pool.shutdown(wait=True)
        assert observations == [expected]
        assert job.code == expected
        assert job.state == ("error" if expected else "done")
        if expected:
            assert not job.dir.exists()
        else:
            assert job.bytes == merged
    finally:
        manager.shutdown()


def test_final_file_cap_is_checked_without_a_watcher_poll(tmp_path):
    def fast(argv, *, cwd, **kwargs):
        (Path(cwd).parent / "out" / "media.mp4").write_bytes(b"x" * 1001)
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"), Limits(max_bytes=1000), runner=fast)
    try:
        job = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        manager._pool.shutdown(wait=True)
        assert job.code == "too_large" and not job.dir.exists()
    finally:
        manager.shutdown()
