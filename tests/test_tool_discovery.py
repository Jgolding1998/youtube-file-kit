import os
from pathlib import Path

from youtube_file_kit.downloader import find_tools


def test_tool_discovery_ignores_current_directory_and_relative_path_entries(tmp_path, monkeypatch):
    cwd, trusted = tmp_path / "working", tmp_path / "trusted bin"
    cwd.mkdir(); trusted.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    for directory in (cwd, trusted):
        for name in ("ffmpeg", "deno"):
            tool = directory / (name + suffix)
            tool.write_bytes(b"OWNED NONEXECUTED DISCOVERY FIXTURE")
            tool.chmod(0o700)
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(cwd), str(trusted)]))
    tools = find_tools()
    assert tools.ffmpeg == str((trusted / ("ffmpeg" + suffix)).resolve())
    assert tools.deno == str((trusted / ("deno" + suffix)).resolve())


def test_tool_discovery_skips_a_denied_path_entry(tmp_path, monkeypatch):
    denied, trusted = tmp_path / "denied", tmp_path / "trusted"
    denied.mkdir(); trusted.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "deno"):
        tool = trusted / (name + suffix)
        tool.write_bytes(b"OWNED NONEXECUTED DISCOVERY FIXTURE"); tool.chmod(0o700)
    original = Path.is_file
    def is_file(path):
        if path.parent == denied:
            raise PermissionError("Owned denied-entry fixture")
        return original(path)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setenv("PATH", os.pathsep.join([str(denied), str(trusted)]))
    tools = find_tools()
    assert tools.ffmpeg == str((trusted / ("ffmpeg" + suffix)).resolve())
    assert tools.deno == str((trusted / ("deno" + suffix)).resolve())
