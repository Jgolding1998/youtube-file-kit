"""Strict parsing of a single YouTube video link into one canonical URL."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

MAX_URL_LENGTH = 300
_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_SHORT_HOST = "youtu.be"
# Share links commonly carry these; they are dropped from the canonical URL.
_IGNORED_PARAMS = {"si", "t", "feature", "pp"}
_PARAM_VALUE = re.compile(r"[A-Za-z0-9_.\-]{0,64}")


class InvalidURL(ValueError):
    """The text is not one supported public YouTube video link."""


def _video_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise InvalidURL("bad video id")
    return value


def canonical_video_url(text: str) -> str:
    """Return https://www.youtube.com/watch?v=<id> or raise InvalidURL.

    Accepts youtube.com/watch?v=, youtu.be/<id> and youtube.com/shorts/<id>.
    Rejects playlists, credentials, ports, other schemes and hosts, fragments,
    encoded or non-ASCII characters and any unexpected query fields.
    """
    if not isinstance(text, str):
        raise InvalidURL("not text")
    text = text.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise InvalidURL("empty or too long")
    if not text.isascii() or any(c.isspace() or ord(c) < 0x21 or ord(c) == 0x7F for c in text):
        raise InvalidURL("unexpected characters")
    if "%" in text or "\\" in text or "@" in text:
        raise InvalidURL("unexpected characters")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidURL("scheme")
    if parts.username is not None or parts.password is not None:
        raise InvalidURL("credentials")
    if parts.fragment or "#" in text:
        raise InvalidURL("fragment")
    netloc = parts.netloc.lower()
    if ":" in netloc or netloc != (parts.hostname or ""):
        raise InvalidURL("port or malformed host")
    host = netloc

    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False) if parts.query else []
    if parts.query and len(pairs) != parts.query.count("&") + 1:
        raise InvalidURL("malformed query")
    names = [k for k, _ in pairs]
    if len(names) != len(set(names)):
        raise InvalidURL("repeated field")
    params = dict(pairs)

    if host in _WATCH_HOSTS:
        path = parts.path
        if path == "/watch":
            vid = _video_id(params.pop("v", ""))
        elif path.startswith("/shorts/") and path.count("/") == 2:
            vid = _video_id(path[len("/shorts/"):])
        else:
            raise InvalidURL("unsupported path")
    elif host == _SHORT_HOST:
        path = parts.path
        if path.count("/") != 1:
            raise InvalidURL("unsupported path")
        vid = _video_id(path[1:])
    else:
        raise InvalidURL("host")

    for key, value in params.items():
        if key not in _IGNORED_PARAMS or not _PARAM_VALUE.fullmatch(value):
            raise InvalidURL("unexpected field")
    return f"https://www.youtube.com/watch?v={vid}"
