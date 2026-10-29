import pytest

from youtube_file_kit.urls import InvalidURL, canonical_video_url

CANON = "https://www.youtube.com/watch?v=BaW_jenozKc"


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/watch?v=BaW_jenozKc",
    "http://youtube.com/watch?v=BaW_jenozKc",
    "https://m.youtube.com/watch?v=BaW_jenozKc",
    "www.youtube.com/watch?v=BaW_jenozKc",
    "  https://youtu.be/BaW_jenozKc  ",
    "https://youtu.be/BaW_jenozKc?si=AbC-12_x",
    "https://www.youtube.com/watch?v=BaW_jenozKc&t=42",
    "https://www.youtube.com/watch?feature=shared&v=BaW_jenozKc",
    "https://www.youtube.com/shorts/BaW_jenozKc",
    "HTTPS://WWW.YOUTUBE.COM/watch?v=BaW_jenozKc",
])
def test_accepts_supported_links(text):
    assert canonical_video_url(text) == CANON


@pytest.mark.parametrize("text", [
    "",
    "BaW_jenozKc",
    "https://www.youtube.com/watch?v=BaW_jenozK",            # 10 chars
    "https://www.youtube.com/watch?v=BaW_jenozKcc",          # 12 chars
    "https://www.youtube.com/watch?v=BaW_jenozKc&list=PL123",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/watch?v=BaW_jenozKc&v=aaaaaaaaaaa",
    "https://www.youtube.com/watch?v=BaW_jenozKc&index=2",
    "https://www.youtube.com/watch?v=BaW_jenozKc&x=1",
    "https://www.youtube.com/watch?v=BaW_jenozKc#t=1",
    "https://user:pw" + "@" + "www.youtube.com/watch?v=BaW_jenozKc",
    "https://www.youtube.com" + "@" + "evil.example/watch?v=BaW_jenozKc",
    "https://www.youtube.com:8443/watch?v=BaW_jenozKc",
    "https://www.youtube.com./watch?v=BaW_jenozKc",
    "https://evil.example/watch?v=BaW_jenozKc",
    "https://youtube.com.evil.example/watch?v=BaW_jenozKc",
    "https://127.0.0.1/watch?v=BaW_jenozKc",
    "https://localhost/watch?v=BaW_jenozKc",
    "https://[::1]/watch?v=BaW_jenozKc",
    "https://music.youtube.com/watch?v=BaW_jenozKc",
    "file:///C:/Windows/win.ini",
    "ftp://youtube.com/watch?v=BaW_jenozKc",
    "javascript:alert(1)",
    "ytsearch:cats",
    "https://www.youtube.com/watch?v=BaW%5FjenozKc",
    "https://www.youtube.com/watch?v=BaW_jenozKc\nhttps://evil.example",
    "https://www.youtube.com/watch?v=BaW_jenozKc --exec calc",
    "--exec=calc",
    "https://www.youtube.com/embed/BaW_jenozKc",
    "https://www.youtube.com/shorts/BaW_jenozKc/extra",
    "https://youtu.be/BaW_jenozKc/extra",
    "https://www.youtube.com/watch\\?v=BaW_jenozKc",
    "https://www.youtube.com/watch?v=BaW_jenozKc&si=a b",
    "https://www.youtube.com/watch?v=BaW_jenozKc&&",
    "https://www.y\u043eutube.com/watch?v=BaW_jenozKc",       # Cyrillic o
    "https://www.youtube.com/watch?v=" + "a" * 400,
])
def test_rejects_everything_else(text):
    with pytest.raises(InvalidURL):
        canonical_video_url(text)


def test_rejects_non_string():
    with pytest.raises(InvalidURL):
        canonical_video_url(["https://youtu.be/BaW_jenozKc"])
