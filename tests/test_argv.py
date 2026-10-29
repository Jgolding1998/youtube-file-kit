"""The yt-dlp command line is fixed; check it with yt-dlp's own option parser."""

import os
from pathlib import Path

import pytest
import yt_dlp

from youtube_file_kit.downloader import Limits, Tools, build_argv, build_env, classify

URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
FORBIDDEN = {"--exec", "--cookies", "--cookies-from-browser", "--netrc", "--netrc-cmd", "--username",
             "--password", "--config-locations", "--plugin-dirs", "--remote-components", "--load-info-json",
             "--batch-file", "--external-downloader-args", "--postprocessor-args", "--use-postprocessor",
             "--enable-file-urls", "--no-check-certificates", "--update", "--update-to", "--add-header"}


def parse(tools):
    argv = build_argv(URL, Path("job"), tools, Limits())
    start = argv.index("yt_dlp") + 1
    assert argv[:start] == [tools.python, "-I", "-X", "utf8", "-m", "yt_dlp"]
    return argv, yt_dlp.parse_options(argv[start:])


@pytest.mark.parametrize("tools", [
    Tools(python="python", ffmpeg="/opt/ffmpeg", deno="/opt/deno"),
    Tools(python="python"),
])
# parse_options runs the same option logic as the CLI but skips config files;
# config-file handling is covered by test_planted_config_files_are_ignored.
def test_effective_options_are_locked_down(tools):
    argv, parsed = parse(tools)
    opts, ydl = parsed.options, parsed.ydl_opts
    assert not FORBIDDEN & set(argv)
    assert argv[-2:] == ["--", URL] and parsed.urls == [URL]
    assert opts.ignoreconfig and opts.config_locations is None
    assert opts.plugin_dirs == []
    assert ydl["remote_components"] == []
    assert ydl["js_runtimes"] == ({"deno": {"path": "/opt/deno"}} if tools.deno else {})
    assert ydl["cookiefile"] is None and ydl["cookiesfrombrowser"] is None
    assert not ydl["usenetrc"] and ydl["netrc_cmd"] is None
    assert ydl.get("exec_cmd") is None
    assert ydl["external_downloader"] == {"default": "native"}
    assert ydl["allowed_extractors"] == ["youtube"]
    assert ydl["noplaylist"] and ydl["max_downloads"] is None
    assert ydl["cachedir"] is False and ydl["proxy"] == ""
    assert not ydl["enable_file_urls"] and not ydl["nocheckcertificate"]
    assert ydl["overwrites"] is False and ydl["continuedl"] is False
    assert ydl["outtmpl"] == {"default": "media.%(ext)s"}
    assert ydl["max_filesize"] == Limits().max_bytes
    keys = {pp["key"] for pp in ydl["postprocessors"]}
    assert keys <= {"FFmpegConcat"}  # yt-dlp default, playlist-only
    assert "[protocol=https]" in ydl["format"]


def test_yt_dlp_loads_only_the_youtube_extractor():
    ydl = yt_dlp.YoutubeDL({"allowed_extractors": ["youtube"], "quiet": True})
    assert sorted(ydl._ies) == ["Youtube"]


def test_env_is_minimal(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    env = build_env(tmp_path, Tools(python="python", ffmpeg=str(tmp_path / "bin" / "ffmpeg")))
    assert "AWS_SECRET_ACCESS_KEY" not in env and "HTTPS_PROXY" not in env and "PYTHONPATH" not in env
    assert env["YTDLP_NO_PLUGINS"] == "1" and env["DENO_NO_UPDATE_CHECK"] == "1"
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        assert env[key] == str(tmp_path / "home")
    assert env["PATH"] == str(tmp_path / "bin")
    assert set(env) - {"SYSTEMROOT", "WINDIR", "LANG"} <= {
        "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
        "XDG_DATA_HOME", "TEMP", "TMP", "TMPDIR", "DENO_DIR", "DENO_NO_UPDATE_CHECK", "DENO_NO_PROMPT",
        "YTDLP_NO_PLUGINS", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONUTF8", "NO_COLOR"}
    if os.name == "nt":
        assert env["SYSTEMROOT"]


@pytest.mark.parametrize("stderr, code", [
    (b"ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    (b"ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "age"),
    (b"ERROR: [youtube] x: The uploader has not made this video available in your country", "region"),
    (b"ERROR: [youtube] x: This video is DRM protected", "drm"),
    (b"ERROR: Join this channel to get access to members-only content", "members"),
    ("ERROR: Sign in to confirm you’re not a bot".encode(), "blocked"),
    (b"ERROR: This content isn't available. The current session has been rate-limited by YouTube", "rate_limited"),
    (b"ERROR: [youtube] x: This live event will begin in 3 hours.", "live"),
    (b"ERROR: [youtube] x: Video unavailable", "unavailable"),
    (b"ERROR: [youtube] BaW_jenozKc: This video is unavailable", "unavailable"),  # seen live 2026-10-29
    (b"ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>", "network"),
    (b"ERROR: Requested format is not available", "no_format"),
    (b"something unexpected", "failed"),
    (b"", "failed"),
])
def test_classify(stderr, code):
    assert classify(stderr) == code
