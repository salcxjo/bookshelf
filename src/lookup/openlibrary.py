"""Open Library client: ISBN lookup, work search and synopses."""
import os
import re

import requests

from bookshelf.isbn import is_valid_isbn13
from bookshelf.textnorm import norm

API_URL = "https://openlibrary.org/api/books"
SEARCH_URL = "https://openlibrary.org/search.json"
BASE_URL = "https://openlibrary.org"
TIMEOUT = 10  # seconds

def _headers() -> dict:
    """Open Library asks API users to identify themselves. Set OPENLIBRARY_CONTACT (in .env) to
    an e-mail address or URL if you make heavy use of it. Read per request, so .env changes apply."""
    return {"User-Agent": f"Bookshelf/0.1 ({os.environ.get('OPENLIBRARY_CONTACT', 'personal library app')})"}


def clean_isbn(raw: str) -> str:
    """Strip spaces and hyphens: '978-0-14-312854-0' -> '9780143128540'."""
    return re.sub(r"[\s-]", "", raw)


def _parse_year(publish_date: str | None) -> int | None:
    """Pull a 4-digit year out of strings like 'March 2003' or '1988'."""
    if not publish_date:
        return None
    match = re.search(r"\b(\d{4})\b", publish_date)
    return int(match.group(1)) if match else None


def _usable_cover(url: str | None) -> str | None:
    """Open Library uses cover id -1 as its 'no cover' placeholder."""
    if not url or "/id/-1-" in url:
        return None
    return url


def _get_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, headers=_headers(), timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _get_json_or_none(url: str, params: dict | None = None) -> dict | None:
    """Like _get_json, but a 404 means 'no such record' and returns None."""
    response = requests.get(url, params=params, headers=_headers(), timeout=TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _short_key(key: str | None) -> str | None:
    """'/works/OL45883W' -> 'OL45883W'"""
    return key.rsplit("/", 1)[-1] if key else None


# --------------------------------------------------------------------------- #
# ISBN -> book
# --------------------------------------------------------------------------- #

def fetch_by_isbn(isbn: str) -> dict | None:
    """Return book metadata for an ISBN, or None if Open Library doesn't know it.

    Raises requests.RequestException on network/server errors, so callers can
    tell "not found" apart from "lookup failed".
    """
    isbn = clean_isbn(isbn)
    key = f"ISBN:{isbn}"
    data = _get_json(API_URL, {"bibkeys": key, "format": "json", "jscmd": "data"})

    result = data.get(key)  # unknown ISBN -> {} -> None
    if not result:
        return None

    publishers = result.get("publishers", [])
    return {
        "isbn": isbn,
        "title": result.get("title"),
        "subtitle": result.get("subtitle"),
        "authors": [a["name"] for a in result.get("authors", [])],
        "publisher": publishers[0]["name"] if publishers else None,
        "year": _parse_year(result.get("publish_date")),
        "cover_url": _usable_cover(result.get("cover", {}).get("large")),
    }


# --------------------------------------------------------------------------- #
# Text search
# --------------------------------------------------------------------------- #

def _best_isbn13(isbns: list[str]) -> str | None:
    return next((i for i in isbns if is_valid_isbn13(i)), None)


def search_works(*, q: str | None = None, title: str | None = None,
                 author: str | None = None, limit: int = 10) -> list[dict]:
    """Search Open Library. Results are *works* (a book regardless of edition), each with
    one representative ISBN-13 if any edition has one."""
    params: dict = {"fields": "key,title,author_name,first_publish_year,cover_i,isbn,edition_count",
                    "limit": limit}
    if q:
        params["q"] = q
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    if not (q or title or author):
        return []

    results = []
    for doc in _get_json(SEARCH_URL, params).get("docs", []):
        if not doc.get("title"):
            continue
        cover_id = doc.get("cover_i")
        results.append({
            "work_key": _short_key(doc.get("key")),
            "title": doc["title"],
            "authors": doc.get("author_name", [])[:5],
            "year": doc.get("first_publish_year"),
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else None,
            "isbn": _best_isbn13(doc.get("isbn", [])),
            "edition_count": doc.get("edition_count"),
        })
    return results


def find_work_key(title: str, authors: list[str] | tuple = ()) -> str | None:
    """Best-effort: the work whose title matches exactly (ignoring case and accents)."""
    hits = search_works(title=title, author=authors[0] if authors else None, limit=5)
    return next((h["work_key"] for h in hits if norm(h["title"]) == norm(title)), None)


# --------------------------------------------------------------------------- #
# Synopsis
# --------------------------------------------------------------------------- #

def _text(value) -> str:
    """Descriptions are either a plain string or {'type': '/type/text', 'value': '...'}."""
    if isinstance(value, dict):
        value = value.get("value")
    return value if isinstance(value, str) else ""


def clean_description(text: str) -> str:
    """Drop the source notes and markdown links Open Library descriptions often carry."""
    text = text.replace("\r\n", "\n")
    text = re.split(r"\n\s*-{5,}\s*\n", text, maxsplit=1)[0]          # notes after a ---------- line
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)                # [label](url) -> label
    text = re.sub(r"^\s*\[\d+\]:\s*\S+.*$", "", text, flags=re.M)       # [1]: http://... reference lines
    text = re.sub(r"\(\[source\]\[\d+\]\)", "", text)                   # ([source][1])
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_description(*, isbn: str | None = None, work_key: str | None = None,
                      title: str | None = None, authors: list[str] | tuple = ()) -> tuple[str | None, str]:
    """Find a book's synopsis. Returns (work_key, description); description is '' when
    Open Library has none. Tries the work record first, then the edition's own text.

    Raises requests.RequestException on network/server errors.
    """
    edition_text = ""
    if not work_key and isbn:
        edition = _get_json_or_none(f"{BASE_URL}/isbn/{isbn}.json")  # redirects to the edition record
        if edition:
            edition_text = _text(edition.get("description"))
            works = edition.get("works") or []
            if works:
                work_key = _short_key(works[0].get("key"))
    if not work_key and title:
        work_key = find_work_key(title, authors)

    work_text = ""
    if work_key:
        work = _get_json_or_none(f"{BASE_URL}/works/{work_key}.json")
        if work:
            work_text = _text(work.get("description"))

    return work_key, clean_description(work_text or edition_text)
