"""Run copied launchers with local module fakes; no packages or videos are fetched."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]


def shell_for(kind):
    if kind == "powershell":
        return shutil.which("pwsh") or shutil.which("powershell")
    if kind == "windows-powershell":
        return shutil.which("powershell") if os.name == "nt" else None
    found = shutil.which("sh")
    if found:
        return found
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.fixture(params=["powershell", "windows-powershell", "posix"])
def launcher(request, tmp_path):
    if request.param != "posix" and os.name != "nt":
        pytest.skip("The PowerShell launcher targets Windows")
    shell = shell_for(request.param)
    if not shell:
        pytest.skip(f"{request.param} shell is not installed")
    project = tmp_path / "launcher project"
    project.mkdir()
    for name in ("run.ps1", "run.sh"):
        shutil.copyfile(ROOT / name, project / name)
    requirements = project / "requirements.txt"
    requirements.write_text("local-fixture==1\n", encoding="utf-8")
    environment = project / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    if os.name == "nt":
        python = environment / "Scripts/python.exe"
        # Git's POSIX shell can run a tiny wrapper around the same local venv.
        wrapper = environment / "bin/python"
        wrapper.parent.mkdir(exist_ok=True)
        wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(python.as_posix())} \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
    log = project / "calls.jsonl"
    for module, exit_variable in (("pip", "FAKE_INSTALL_EXIT"), ("youtube_file_kit", "FAKE_APP_EXIT")):
        (project / f"{module}.py").write_text(
            "import json, os, pathlib, sys\n"
            f"row = {{'module': {module!r}, 'args': sys.argv[1:]}}\n"
            "with pathlib.Path(os.environ['LAUNCHER_LOG']).open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps(row) + '\\n')\n"
            f"raise SystemExit(int(os.environ.get({exit_variable!r}, '0')))\n",
            encoding="utf-8",
        )
    env = os.environ.copy()
    env.update(LAUNCHER_LOG=str(log), PYTHONDONTWRITEBYTECODE="1", FAKE_INSTALL_EXIT="0", FAKE_APP_EXIT="0")
    if request.param != "posix":
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(project / "run.ps1")]
    else:
        command = [shell, (project / "run.sh").as_posix()]

    def run(*args):
        return subprocess.run(command + list(args), cwd=tmp_path, env=env, text=True, capture_output=True, timeout=15)

    def calls():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return project, requirements, env, run, calls


def digest(requirements):
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def test_app_exit_code_and_arguments_are_preserved(launcher):
    project, requirements, env, run, calls = launcher
    (project / ".venv/.installed").write_text(digest(requirements) + "\n", encoding="ascii")
    env["FAKE_APP_EXIT"] = "23"
    result = run("--port", "8123", "two words")
    assert result.returncode == 23, result.stderr
    assert calls() == [{"module": "youtube_file_kit", "args": ["--port", "8123", "two words"]}]


def test_legacy_and_changed_requirements_reinstall_but_unchanged_requirements_do_not(launcher):
    project, requirements, _, run, calls = launcher
    marker = project / ".venv/.installed"
    marker.touch()  # Older releases wrote an empty marker.
    assert run().returncode == 0
    assert [row["module"] for row in calls()] == ["pip", "youtube_file_kit"]
    assert calls()[0]["args"] == ["install", "--require-hashes", "--only-binary=:all:", "-r", "requirements.txt"]
    assert marker.read_text(encoding="ascii").strip() == digest(requirements)
    assert run().returncode == 0
    assert [row["module"] for row in calls()] == ["pip", "youtube_file_kit", "youtube_file_kit"]
    requirements.write_text("local-fixture==2\n", encoding="utf-8")
    assert run().returncode == 0
    assert [row["module"] for row in calls()] == ["pip", "youtube_file_kit", "youtube_file_kit", "pip", "youtube_file_kit"]
    assert marker.read_text(encoding="ascii").strip() == digest(requirements)


def test_failed_install_does_not_launch_or_stamp_current_requirements_and_can_retry(launcher):
    project, requirements, env, run, calls = launcher
    marker = project / ".venv/.installed"
    marker.write_text("old requirements\n", encoding="ascii")
    env["FAKE_INSTALL_EXIT"] = "17"
    result = run()
    assert result.returncode == 17, result.stderr
    assert [row["module"] for row in calls()] == ["pip"]
    assert marker.read_text(encoding="ascii").strip() != digest(requirements)
    env["FAKE_INSTALL_EXIT"] = "0"
    assert run().returncode == 0
    assert [row["module"] for row in calls()] == ["pip", "pip", "youtube_file_kit"]
    assert marker.read_text(encoding="ascii").strip() == digest(requirements)
