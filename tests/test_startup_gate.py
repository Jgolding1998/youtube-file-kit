import json
import os
import signal
import sys
import threading
import time
import venv
from unittest.mock import patch

import pytest

from youtube_file_kit import procguard
from test_procguard import alive, env


WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object startup gate")
BASE = getattr(sys, "_base_executable", None) or sys.executable


def target(marker):
    return [BASE, "-I", "-c", f"open({str(marker)!r},'w').write('started')"]


@pytest.mark.parametrize("reason", ["cancelled", "timeout"])
def test_stopped_before_launch_starts_no_process(tmp_path, reason):
    cancel = threading.Event()
    if reason == "cancelled":
        cancel.set()
    with patch.object(procguard.subprocess, "Popen", side_effect=AssertionError("Must not launch")):
        result = procguard.run_bounded(target(tmp_path / "started"), cwd=str(tmp_path), env=env(),
                                      timeout=0 if reason == "timeout" else 10, cancel=cancel)
    assert result.stopped == reason and result.returncode is None


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), True, 10**400])
def test_unbounded_timeout_is_rejected_without_launch(tmp_path, timeout):
    with patch.object(procguard.subprocess, "Popen", side_effect=AssertionError("Must not launch")), pytest.raises(ValueError):
        procguard.run_bounded(target(tmp_path / "started"), cwd=str(tmp_path), env=env(), timeout=timeout)


@WINDOWS
def test_delayed_assignment_cannot_start_target_or_escape_grandchildren(tmp_path, monkeypatch):
    pid_file = tmp_path / "owned-child.pid"
    original = procguard._WindowsJob.assign

    def delayed(self, pid):
        time.sleep(0.3)
        assert not pid_file.exists()
        original(self, pid)

    monkeypatch.setattr(procguard._WindowsJob, "assign", delayed)
    code = ("import subprocess,sys\np=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])\n"
            f"open({str(pid_file)!r},'w').write(str(p.pid))\n")
    kid = None
    try:
        result = procguard.run_bounded([BASE, "-I", "-c", code], cwd=str(tmp_path), env=env(), timeout=10)
        assert result.returncode == 0 and result.stopped is None
        kid = int(pid_file.read_text())
        assert not alive(kid)
    finally:
        if kid is None and pid_file.exists():
            kid = int(pid_file.read_text())
        if kid is not None and alive(kid):
            os.kill(kid, signal.SIGTERM)


@WINDOWS
@pytest.mark.parametrize("when", ["before_assignment", "during_assignment", "timeout_during_assignment", "assignment_denied", "parent_exited"])
def test_startup_failure_or_stop_never_releases_target(tmp_path, monkeypatch, when):
    marker = tmp_path / "target-started"
    cancel = threading.Event()
    created, jobs = [], []
    original_popen, original_assign = procguard.subprocess.Popen, procguard._WindowsJob.assign

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        created.append(process)
        if when == "before_assignment":
            cancel.set()
        return process

    def assign(self, pid):
        jobs.append(self)
        if when == "before_assignment":
            raise AssertionError("Cancelled startup must not assign")
        if when == "assignment_denied":
            raise OSError("Assignment refused")
        if when == "parent_exited":
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            return original_assign(self, pid)
        if when == "timeout_during_assignment":
            time.sleep(0.2)
        original_assign(self, pid)
        if when == "during_assignment":
            cancel.set()

    monkeypatch.setattr(procguard.subprocess, "Popen", popen)
    monkeypatch.setattr(procguard._WindowsJob, "assign", assign)
    started = time.monotonic()
    if when in {"assignment_denied", "parent_exited"}:
        with pytest.raises(OSError):
            procguard.run_bounded(target(marker), cwd=str(tmp_path), env=env(), timeout=5, cancel=cancel)
    else:
        result = procguard.run_bounded(target(marker), cwd=str(tmp_path), env=env(),
                                      timeout=0.05 if when == "timeout_during_assignment" else 5, cancel=cancel)
        assert result.stopped == ("timeout" if when == "timeout_during_assignment" else "cancelled")
    assert time.monotonic() - started < 3
    assert not marker.exists()
    assert all(process.poll() is not None and process.stdin.closed and process.stdout.closed and process.stderr.closed for process in created)
    assert all(job.handle is None for job in jobs)


@WINDOWS
def test_gate_is_isolated_and_preserves_target_arguments_environment_pipes_and_exit(tmp_path):
    imported = tmp_path / "unexpected-import"
    (tmp_path / "subprocess.py").write_text(f"open({str(imported)!r},'w').write('bad')\nraise RuntimeError()", encoding="utf-8")
    child_env = {**env(), "PYTHONPATH": str(tmp_path), "PYTHONHOME": str(tmp_path), "OWNED_TEST_SENTINEL": "synthetic-value"}
    values = ["--flag", "value with spaces", "$(no-shell)", ";"]
    code = "import json,sys,os;print(json.dumps([sys.argv[1:],sys.stdin.read(),os.environ['OWNED_TEST_SENTINEL']]));sys.stderr.write('err');sys.exit(7)"
    result = procguard.run_bounded([BASE, "-I", "-c", code, *values], cwd=str(tmp_path), env=child_env, timeout=10)
    assert result.returncode == 7 and result.stopped is None and result.stderr == b"err"
    assert json.loads(result.stdout) == [values, "", "synthetic-value"]
    assert not imported.exists()


@WINDOWS
def test_one_tool_process_budget_reserves_the_gate_without_blocking_the_tool(tmp_path):
    result = procguard.run_bounded([BASE, "-I", "-c", "print('tool completed')"], cwd=str(tmp_path),
                                  env=env(), timeout=10, max_processes=1)
    assert result.returncode == 0 and result.stdout.strip() == b"tool completed"


@WINDOWS
def test_extra_gate_slot_does_not_expand_the_tool_process_budget(tmp_path):
    marker = tmp_path / "unwanted-child"
    child = f"open({str(marker)!r},'w').write('started')"
    code = "import subprocess,sys\nsubprocess.run([sys.executable,'-I','-c'," + repr(child) + "],check=True)\n"
    result = procguard.run_bounded([BASE, "-I", "-c", code], cwd=str(tmp_path), env=env(),
                                  timeout=10, max_processes=1)
    assert result.returncode != 0 and not marker.exists()


@WINDOWS
@pytest.mark.parametrize("code", [0, 1, 101, 255, 0x7FFFFFFF, 0x80000000, 0xC0000005, 0xC0000409, 0xFFFFFFFF])
def test_gate_preserves_full_windows_exit_status(tmp_path, code):
    target_code = ("import ctypes\nexit=ctypes.WinDLL('kernel32').ExitProcess\n"
                   "exit.argtypes=[ctypes.c_uint]\nexit.restype=None\nexit(" + str(code) + ")\n")
    result = procguard.run_bounded([BASE, "-I", "-c", target_code], cwd=str(tmp_path), env=env(),
                                  timeout=10, max_processes=1)
    assert result.stopped is None and result.returncode == code


@WINDOWS
@pytest.mark.parametrize("stop", ["identity_mismatch", "assignment_denied", "cancel_during_assignment", "timeout_during_assignment"])
def test_redirecting_interpreter_cannot_run_a_target_outside_the_assigned_job(tmp_path, monkeypatch, stop):
    environment = tmp_path / "redirector environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    monkeypatch.setattr(procguard.sys, "_base_executable", str(environment / "Scripts" / "python.exe"))
    marker = tmp_path / "target-started"
    cancel = threading.Event()
    original = procguard._WindowsJob.assign

    def delayed(self, pid):
        time.sleep(0.3)
        assert not marker.exists()
        if stop == "assignment_denied":
            raise OSError("Assignment refused")
        original(self, pid)
        if stop == "cancel_during_assignment":
            cancel.set()

    monkeypatch.setattr(procguard._WindowsJob, "assign", delayed)
    started = time.monotonic()
    if stop == "assignment_denied":
        with pytest.raises(OSError):
            procguard.run_bounded(target(marker), cwd=str(tmp_path), env=env(), timeout=5)
    else:
        result = procguard.run_bounded(target(marker), cwd=str(tmp_path), env=env(), cancel=cancel,
                                      timeout=0.05 if stop == "timeout_during_assignment" else 5)
        if stop == "identity_mismatch":
            assert result.returncode == 125 and result.stopped is None
        else:
            assert result.stopped == ("timeout" if stop == "timeout_during_assignment" else "cancelled")
    assert not marker.exists() and time.monotonic() - started < 3


@WINDOWS
def test_helper_exit_after_assignment_is_collected_even_when_release_pipe_is_broken(tmp_path, monkeypatch):
    marker = tmp_path / "target-started"
    original = procguard._WindowsJob.assign

    def assign_then_exit(self, pid):
        original(self, pid)
        self.terminate()
        deadline = time.monotonic() + 2
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.01)

    monkeypatch.setattr(procguard._WindowsJob, "assign", assign_then_exit)
    result = procguard.run_bounded(target(marker), cwd=str(tmp_path), env=env(), timeout=5)
    assert result.returncode == 1 and result.stopped is None and not marker.exists()


@WINDOWS
def test_watcher_exception_reaps_process_and_descendant_and_closes_pipes(tmp_path, monkeypatch):
    marker = tmp_path / "owned-child.pid"
    created = []
    original = procguard.subprocess.Popen

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process

    def watch():
        if marker.exists():
            raise RuntimeError("Synthetic watcher failure")

    monkeypatch.setattr(procguard.subprocess, "Popen", popen)
    code = ("import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])\n"
            f"open({str(marker)!r},'w').write(str(p.pid))\ntime.sleep(60)\n")
    kid = None
    try:
        with pytest.raises(RuntimeError, match="Synthetic watcher failure"):
            procguard.run_bounded([BASE, "-I", "-c", code], cwd=str(tmp_path), env=env(), timeout=10, watch=watch)
        kid = int(marker.read_text())
        assert not alive(kid)
        assert all(process.poll() is not None and process.stdout.closed and process.stderr.closed for process in created)
    finally:
        if kid is None and marker.exists():
            kid = int(marker.read_text())
        if kid is not None and alive(kid):
            os.kill(kid, signal.SIGTERM)
