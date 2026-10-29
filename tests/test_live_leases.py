import http.client
import socket
import threading
import time

import anyio
import pytest
import uvicorn

from youtube_file_kit.app import create_app
from youtube_file_kit.downloader import Limits, Tools
from test_file_leases import PAYLOAD, completed


@pytest.mark.parametrize("disconnect", [False, True])
def test_loopback_transfer_survives_expiry_or_releases_on_disconnect(tmp_path, disconnect):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = create_app(port=port, root=tmp_path / "runtime", tools=Tools(python="unused"),
                     limits=Limits(job_ttl=60))
    manager = app.state.manager
    job = completed(app)
    started, resume = threading.Event(), threading.Event()

    async def paused(scope, receive, send):
        async def forward(message):
            await send(message)
            if scope["type"] == "http" and scope["path"].endswith("/file") and message["type"] == "http.response.start" and message["status"] == 200:
                started.set()
                while not resume.is_set():
                    await anyio.sleep(0.005)

        await app(scope, receive, forward)

    server = uvicorn.Server(uvicorn.Config(paused, host="127.0.0.1", port=port, lifespan="on", http="h11",
                                         log_level="critical", access_log=False, server_header=False,
                                         proxy_headers=False, ws="none", timeout_graceful_shutdown=2))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    response = None
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        connection.request("GET", f"/api/jobs/{job.id}/file")
        response = connection.getresponse()
        assert response.status == 200 and started.wait(2)
        job.finished = time.monotonic() - 61
        manager.sweep()
        assert manager.get(job.id) is None and job.file_path.is_file() and job.readers == 1
        if disconnect:
            response.close()
            connection.close()
        else:
            resume.set()
            assert response.read() == PAYLOAD
        deadline = time.monotonic() + 3
        while (job.readers or job.dir.exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job.readers == 0 and not job.dir.exists()
    finally:
        resume.set()
        if response is not None:
            response.close()
        connection.close()
        server.should_exit = True
        worker.join(5)
        listener.close()
        assert not worker.is_alive()
        assert not manager.root.exists()
