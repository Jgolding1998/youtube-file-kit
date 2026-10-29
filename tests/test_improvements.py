"""Regression tests for flaws found in the security review and docs comparison."""

import os
import time

import yt_dlp_ejs
from yt_dlp.extractor.youtube.jsc._builtin import vendor

from conftest import wait_done
from youtube_file_kit import runtime


def test_deeply_nested_json_is_a_clean_400(make_client):
    client = make_client()
    body = "[" * 1500 + "]" * 1500
    r = client.post("/api/jobs", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_url"


def test_finished_downloads_are_bounded(make_client):
    client = make_client(max_kept=2)
    ids = []
    for _ in range(4):
        r = client.post("/api/jobs", json={"url": "https://youtu.be/okokokokok1"})
        ids.append(r.json()["id"])
        wait_done(client, ids[-1])
    manager = client.app_state.manager
    kept = [i for i in ids if manager.get(i) is not None]
    assert kept == ids[-2:]
    assert sorted(p.name for p in manager.root.iterdir()) == sorted(kept)


def test_stale_runtime_folders_are_removed(tmp_path):
    prefix = "youtube-file-kit-"
    stale, fresh, foreign, other = (tmp_path / f"{prefix}old", tmp_path / f"{prefix}new",
                                    tmp_path / f"{prefix}nomarker", tmp_path / "unrelated")
    for d in (stale, fresh, foreign, other):
        d.mkdir()
        (d / "file.bin").write_bytes(b"x")
    for d in (stale, fresh, other):
        runtime.touch(d)
    old = time.time() - 2 * runtime.STALE_SECONDS
    os.utime(stale / runtime.MARKER, (old, old))
    assert runtime.remove_stale(prefix, base=tmp_path) == 1
    assert not stale.exists() and fresh.exists() and foreign.exists() and other.exists()


def test_pinned_ejs_matches_yt_dlp():
    # yt-dlp only uses the local yt-dlp-ejs scripts when major.minor matches; otherwise it would
    # need a remote component, which this app never allows.
    assert yt_dlp_ejs.version.split(".")[:2] == vendor.VERSION.split(".")[:2]


def test_requirements_pin_matches_pyproject():
    from pathlib import Path
    import tomllib

    root = Path(__file__).resolve().parents[1]
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    req = (root / "requirements.txt").read_text()
    for dep in deps:
        assert f"{dep} \\" in req, dep
    assert all("--hash=sha256:" in block for block in req.split("==")[1:])



def test_planted_config_files_are_ignored(tmp_path, monkeypatch):
    # The yt-dlp CLI reads "Portable", "Home" (--paths home:), user and system configs unless the
    # command line says --ignore-config. parseOpts(..., ignore_config_files=False) is that CLI path.
    from yt_dlp.options import parseOpts

    from youtube_file_kit.downloader import Limits, Tools, build_argv

    hostile = "\n".join(["--username bob", "--add-headers Cookie:x", "--write-info-json",
                         "--exec calc", "--cookies c.txt"]) + "\n"
    for folder in ("out", "home", "home/yt-dlp"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "out" / "yt-dlp.conf").write_text(hostile)
    (tmp_path / "home" / "yt-dlp.conf").write_text(hostile)
    (tmp_path / "home" / "yt-dlp" / "config").write_text(hostile)
    for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "home"))
    argv = build_argv("https://www.youtube.com/watch?v=BaW_jenozKc", tmp_path, Tools(python="python"), Limits())
    argv = argv[argv.index("yt_dlp") + 1:]

    _, opts, _ = parseOpts(argv, ignore_config_files=False)
    assert opts.username is None and not opts.writeinfojson and not opts.exec_cmd and opts.cookiefile is None
    assert not any("Cookie" in h for h in (opts.headers or {}))

    # Control: the same files do load once --ignore-config is removed, so the check above is meaningful.
    _, opts, _ = parseOpts([a for a in argv if a != "--ignore-config"], ignore_config_files=False)
    assert opts.username == "bob" and opts.writeinfojson
