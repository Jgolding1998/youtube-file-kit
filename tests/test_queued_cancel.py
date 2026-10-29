import threading

from youtube_file_kit.downloader import JobManager, Limits, Tools
from youtube_file_kit.procguard import RunResult


def test_cancel_waiting_job_releases_slot_without_stopping_running_job(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def run(argv, *, cwd, cancel, **kwargs):
        from pathlib import Path
        calls.append(Path(cwd).parent.name)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        if cancel.is_set():
            return RunResult(None, b"", b"", stopped="cancelled")
        (Path(cwd).parent / "out" / "media.mp4").write_bytes(b"synthetic media")
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"),
                         Limits(max_concurrent=1, max_queued=1), runner=run)
    try:
        first = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        assert entered.wait(3)
        waiting = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        assert manager.cancel(waiting.id)
        assert (waiting.state, waiting.code) == ("error", "cancelled")
        assert not waiting.dir.exists() and first.state == "running"
        replacement = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        release.set()
        manager._pool.shutdown(wait=True)
        assert calls == [first.id, replacement.id]
        assert replacement.state == "done"
        assert manager.cancel(replacement.id)
        assert replacement.state == "done" and replacement.file_path.is_file()
    finally:
        release.set()
        manager.shutdown()


def test_simultaneous_cancels_finish_waiting_job_once(tmp_path):
    entered, release = threading.Event(), threading.Event()
    finishes = []

    def run(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return RunResult(1, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"),
                         Limits(max_concurrent=1, max_queued=1), runner=run)
    original = manager._finish

    def finish(job, code):
        finishes.append(job.id)
        original(job, code)

    manager._finish = finish
    try:
        manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        assert entered.wait(3)
        waiting = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        barrier = threading.Barrier(3)

        def cancel():
            barrier.wait(3)
            manager.cancel(waiting.id)

        threads = [threading.Thread(target=cancel) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(3)
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        assert finishes.count(waiting.id) == 1
        assert (waiting.state, waiting.code) == ("error", "cancelled")
    finally:
        release.set()
        manager.shutdown()


def test_cancel_at_worker_start_never_runs_the_tool(tmp_path):
    entered, proceed = threading.Event(), threading.Event()
    calls = []

    class StartingManager(JobManager):
        def _run(self, job):
            entered.set()
            assert proceed.wait(5)
            super()._run(job)

    manager = StartingManager(tmp_path / "jobs", Tools(python="unused"), Limits(),
                              runner=lambda *args, **kwargs: calls.append(True))
    try:
        job = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        assert entered.wait(3)
        assert manager.cancel(job.id)
        proceed.set()
        manager._pool.shutdown(wait=True)
        assert (job.state, job.code) == ("error", "cancelled")
        assert not calls and not job.dir.exists()
    finally:
        proceed.set()
        manager.shutdown()
