import pytest

from youtube_file_kit.names import safe_stem


@pytest.mark.parametrize("raw, expected", [
    ("My Video", "My Video"),
    ("../../etc/passwd", "etc_passwd"),
    ("a\\b/c", "a_b_c"),
    ("CON", "_CON"),
    ("lpt1.txt", "_lpt1.txt"),
    ("evil‮txt.exe", "eviltxt.exe"),
    ("tab\tand\nnewline", "tabandnewline"),
    ("  ...  ", "fallback"),
    ("", "fallback"),
    ("Café – live", "Café _ live"),
    ('quote"<>|?*:', "quote"),
])
def test_safe_stem(raw, expected):
    assert safe_stem(raw, "fallback") == expected


def test_length_capped():
    assert len(safe_stem("x" * 500, "f", max_len=80)) == 80
