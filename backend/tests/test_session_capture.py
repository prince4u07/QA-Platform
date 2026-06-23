"""Unit tests for session_capture URL helpers (no browser launched)."""

from modules.projects.session_capture import _origin


def test_origin_extracts_scheme_and_host():
    assert _origin('https://example.com/login?next=1') == 'https://example.com'


def test_origin_with_port():
    assert _origin('http://localhost:5000/a/b') == 'http://localhost:5000'


def test_origin_invalid_returns_empty():
    assert _origin('not a url') == ''
    assert _origin('') == ''
