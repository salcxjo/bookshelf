import pytest
import requests

from lookup import openlibrary

ISBN = "9781234567897"  # fake


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def fake_get_factory(payload, status=200, calls=None):
    def fake_get(url, params=None, timeout=None):
        if calls is not None:
            calls.append(params)
        return FakeResponse(payload, status)

    return fake_get


SAMPLE = {
    f"ISBN:{ISBN}": {
        "title": "Eleven Minutes",
        "authors": [{"name": "Paulo Coelho"}],
        "publishers": [{"name": "Harper"}],
        "publish_date": "March 2003",
        "cover": {"large": "https://example.com/cover.jpg"},
    }
}


def test_found_book(monkeypatch):
    monkeypatch.setattr(openlibrary.requests, "get", fake_get_factory(SAMPLE))
    book = openlibrary.fetch_by_isbn(ISBN)
    assert book["title"] == "Eleven Minutes"
    assert book["authors"] == ["Paulo Coelho"]
    assert book["year"] == 2003
    assert book["cover_url"] == "https://example.com/cover.jpg"


def test_unknown_isbn_returns_none(monkeypatch):
    monkeypatch.setattr(openlibrary.requests, "get", fake_get_factory({}))
    assert openlibrary.fetch_by_isbn(ISBN) is None


def test_hyphens_are_cleaned(monkeypatch):
    calls = []
    monkeypatch.setattr(openlibrary.requests, "get", fake_get_factory(SAMPLE, calls=calls))
    openlibrary.fetch_by_isbn("978-1-234-56789-7")
    assert calls[0]["bibkeys"] == f"ISBN:{ISBN}"


def test_server_error_raises(monkeypatch):
    monkeypatch.setattr(openlibrary.requests, "get", fake_get_factory({}, status=500))
    with pytest.raises(requests.HTTPError):
        openlibrary.fetch_by_isbn(ISBN)


@pytest.mark.parametrize(
    "text, expected",
    [("March 2003", 2003), ("1988", 1988), ("", None), (None, None), ("n.d.", None)],
)
def test_parse_year(text, expected):
    assert openlibrary._parse_year(text) == expected

