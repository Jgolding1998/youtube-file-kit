import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import Busy, Job, JobManager, Limits, Tools
from youtube_file_kit.procguard import RunResult


PAYLOAD = b"synthetic media fixture\x00" * 10000


def completed(app, number=1):
    manager = app.state.manager
    job_id = f"{number:032x}"
    directory = manager.root / job_id
    (directory / "out").mkdir(parents=True)
    path = directory / "out" / "media.mp4"
    path.write_bytes(PAYLOAD)
    job = Job(job_id, "https://www.youtube.com/watch?v=okokokokok1", directory,
              state="done", bytes=len(PAYLOAD), filename="video.mp4", file_path=path,
              finished=time.monotonic())
    manager._jobs[job_id] = job
    return job


@pytest.fixture
def app(tmp_path):
    app = create_app(port=8765, root=tmp_path / "runtime", tools=Tools(python="unused"),
                     limits=Limits(max_kept=1, job_ttl=60))
    yield app
    app.state.manager.shutdown()


def scope(job, *, method="GET", headers=(), version="2.4", extensions=None):
    path = f"/api/jobs/{job.id}/file"
    return {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
            "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 8765),
            "client": ("127.0.0.1", 50001), "http_version": "1.1",
            "headers": [(b"host", b"127.0.0.1:8765"), *headers],
            "asgi": {"version": "3.0", "spec_version": version}, "extensions": extensions or {}}


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.parametrize("trigger", ["TTL", "retention", "shutdown"])
@pytest.mark.parametrize("stage", ["headers", "body"])
def test_retirement_never_removes_a_file_during_its_response(app, trigger, stage):
    manager = app.state.manager
    job = completed(app)
    messages, triggered = [], False

    async def send(message):
        nonlocal triggered
        messages.append(message)
        matching = message["type"] == ("http.response.start" if stage == "headers" else "http.response.body")
        if matching and not triggered:
            triggered = True
            if trigger == "TTL":
                job.finished = time.monotonic() - 61
                manager.sweep()
            elif trigger == "retention":
                manager._trim_finished()
            else:
                manager.shutdown()
            assert manager.get(job.id) is None
            assert manager.acquire_file(job.id) is None
            assert job.file_path.is_file()
            assert job.readers == 1

    asyncio.run(app(scope(job), receive, send))
    assert triggered and messages[0]["status"] == 200
    assert b"".join(message.get("body", b"") for message in messages) == PAYLOAD
    assert job.readers == 0 and not job.dir.exists() and manager.get(job.id) is None
    if trigger == "shutdown":
        assert not manager.root.exists()


def test_multiple_readers_delay_deletion_until_last_release_and_release_is_idempotent(app):
    manager = app.state.manager
    job = completed(app)
    first, second = manager.acquire_file(job.id), manager.acquire_file(job.id)
    assert first and second and job.readers == 2
    job.finished = time.monotonic() - 61
    manager.sweep()
    assert manager.get(job.id) is None and job.dir.exists()
    first.release()
    first.release()
    assert job.readers == 1 and job.file_path.is_file()
    second.release()
    assert job.readers == 0 and not job.dir.exists() and job.id not in manager._jobs


def test_reader_count_zero_can_precede_retired_directory_cleanup(app, monkeypatch):
    from youtube_file_kit import downloader

    manager = app.state.manager
    job = completed(app)
    lease = manager.acquire_file(job.id)
    job.finished = time.monotonic() - 61
    manager.sweep()
    deleting, allow_delete = threading.Event(), threading.Event()
    original = downloader.shutil.rmtree

    def gated_delete(path, **kwargs):
        if Path(path) == job.dir:
            deleting.set()
            assert allow_delete.wait(2)
        return original(path, **kwargs)

    monkeypatch.setattr(downloader.shutil, "rmtree", gated_delete)
    worker = threading.Thread(target=lease.release)
    worker.start()
    try:
        assert deleting.wait(2)
        assert job.readers == 0 and manager.get(job.id) is None
        assert job.dir.exists(), "reader bookkeeping finishes before the unlocked filesystem delete"
    finally:
        allow_delete.set()
        worker.join(3)
    assert not worker.is_alive()
    assert job.readers == 0 and not job.dir.exists()


def test_expired_file_cannot_start_a_new_transfer_before_the_periodic_sweep(app):
    manager = app.state.manager
    job = completed(app)
    job.finished = time.monotonic() - 61
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope(job), receive, send))
    assert messages[0]["status"] == 404
    assert not job.dir.exists() and job.readers == 0


@pytest.mark.parametrize("method,headers,status", [
    ("HEAD", (), 200),
    ("GET", ((b"range", b"bytes=0-99"),), 206),
    ("GET", ((b"range", b"bytes=0-99,1000-1099"),), 206),
    ("GET", ((b"range", b"bytes=9999999-"),), 416),
    ("GET", ((b"range", b"invalid"),), 400),
])
def test_head_and_ranges_release_retired_leases(app, method, headers, status):
    manager = app.state.manager
    job = completed(app)
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            manager._trim_finished()
            assert job.file_path.is_file() and job.readers == 1

    asyncio.run(app(scope(job, method=method, headers=headers), receive, send))
    assert messages[0]["status"] == status
    assert job.readers == 0 and not job.dir.exists()


@pytest.mark.parametrize("exception", [OSError, asyncio.CancelledError])
def test_failed_or_cancelled_send_closes_file_then_releases_lease(app, exception):
    manager = app.state.manager
    job = completed(app)

    async def send(message):
        if message["type"] == "http.response.body":
            manager._trim_finished()
            assert job.file_path.is_file()
            raise exception()

    with pytest.raises(exception):
        asyncio.run(app(scope(job), receive, send))
    assert job.readers == 0 and not job.dir.exists()


def test_asgi_disconnect_cancellation_releases_the_open_file(app):
    manager = app.state.manager
    job = completed(app)

    async def run():
        body_started = anyio.Event()

        async def disconnected():
            await body_started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                manager._trim_finished()
                body_started.set()
                await anyio.sleep_forever()

        with anyio.fail_after(5):
            await app(scope(job, version="2.3"), disconnected, send)

    asyncio.run(run())
    assert job.readers == 0 and not job.dir.exists()


def test_server_pathsend_offload_cannot_outlive_the_file_lease(app):
    manager = app.state.manager
    job = completed(app)
    messages = []

    async def send(message):
        assert message["type"] != "http.response.pathsend"
        messages.append(message)
        if message["type"] == "http.response.start":
            manager._trim_finished()

    asyncio.run(app(scope(job, extensions={"http.response.pathsend": {}}), receive, send))
    assert b"".join(message.get("body", b"") for message in messages) == PAYLOAD
    assert job.readers == 0 and not job.dir.exists()


def test_shutdown_rejects_new_work_without_recreating_runtime(app):
    manager = app.state.manager
    manager.shutdown()
    with pytest.raises(Busy):
        manager.submit("https://www.youtube.com/watch?v=okokokokok1")
    assert not manager.root.exists() and not manager._jobs


def test_retired_pinned_files_remain_charged_to_admission_limit(app):
    manager = app.state.manager
    leases = []
    for number in range(manager._storage_slots):
        job = completed(app, number + 1)
        leases.append(manager.acquire_file(job.id))
    manager._trim_finished()
    assert all(job.retired for job in manager._jobs.values())
    assert len(list(manager.root.iterdir())) == manager._storage_slots
    with pytest.raises(Busy):
        manager.submit("https://www.youtube.com/watch?v=okokokokok1")
    assert len(list(manager.root.iterdir())) == manager._storage_slots
    for lease in leases:
        lease.release()
    assert not manager._jobs and not list(manager.root.iterdir())


def test_releasing_one_retired_file_restores_only_one_reserved_work_slot(app):
    manager = app.state.manager
    leases = [manager.acquire_file(completed(app, number + 1).id) for number in range(manager._storage_slots)]
    manager._trim_finished()
    gate = threading.Event()

    def runner(argv, *, cwd, **kwargs):
        assert gate.wait(5)
        (Path(cwd).parent / "out" / "media.mp4").write_bytes(PAYLOAD)
        return RunResult(0, b"", b"")

    manager.runner = runner
    try:
        leases[0].release()
        manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        with pytest.raises(Busy):
            manager.submit("https://www.youtube.com/watch?v=okokokokok1")
    finally:
        gate.set()
        for lease in leases:
            lease.release()


def test_submit_and_shutdown_cannot_leave_an_unowned_future_or_recreate_runtime(app, monkeypatch):
    manager = app.state.manager
    queued, release, shutdown_started, stopped = (threading.Event() for _ in range(4))
    original = manager._pool.submit
    errors = []

    def submit(*args, **kwargs):
        queued.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def start():
        try:
            manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        except Exception as error:
            errors.append(type(error).__name__)

    def stop():
        shutdown_started.set()
        manager.shutdown()
        stopped.set()

    monkeypatch.setattr(manager._pool, "submit", submit)
    producer = threading.Thread(target=start)
    closer = threading.Thread(target=stop)
    producer.start()
    try:
        assert queued.wait(3)
        closer.start()
        assert shutdown_started.wait(3)
        stopped.wait(0.25)
    finally:
        release.set()
        producer.join(3)
        if closer.ident is not None:
            closer.join(3)
    assert not producer.is_alive() and not closer.is_alive() and stopped.is_set()
    assert errors == [] and not manager._jobs and not manager.root.exists()


def test_pins_do_not_block_admission_while_completed_unleased_files_can_be_evicted(tmp_path):
    gate = threading.Event()

    def runner(argv, *, cwd, **kwargs):
        assert gate.wait(5)
        (Path(cwd).parent / "out" / "media.mp4").write_bytes(PAYLOAD)
        return RunResult(0, b"", b"")

    manager = JobManager(tmp_path / "jobs", Tools(python="unused"), Limits(), runner=runner)
    app = SimpleNamespace(state=SimpleNamespace(manager=manager))
    leases = []
    try:
        for number in range(1, 4):
            job = completed(app, number)
            leases.append(manager.acquire_file(job.id))
            job.finished = time.monotonic() - manager.limits.job_ttl - 1
            manager.sweep()
        available = [completed(app, number) for number in range(4, 11)]
        assert len(manager._jobs) == manager._storage_slots == 10
        job = manager.submit("https://www.youtube.com/watch?v=okokokokok1")
        assert job.state in {"queued", "running"}
        assert manager.get(available[0].id) is None and not available[0].dir.exists()
        assert all(lease.path.is_file() for lease in leases)
        assert sum(j.state != "error" for j in manager._jobs.values()) == manager._storage_slots
    finally:
        gate.set()
        for lease in leases:
            lease.release()
        manager.shutdown()
