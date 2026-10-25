"""Open Library client: ISBN -> normalized book dict."""
import re

import requests

API_URL = "https://openlibrary.org/api/books"
TIMEOUT = 10  # seconds


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


def fetch_by_isbn(isbn: str) -> dict | None:
    """Return book metadata for an ISBN, or None if Open Library doesn't know it.

    Raises requests.RequestException on network/server errors, so callers can
    tell "not found" apart from "lookup failed".
    """
    isbn = clean_isbn(isbn)
    key = f"ISBN:{isbn}"
    response = requests.get(
        API_URL,
        params={"bibkeys": key, "format": "json", "jscmd": "data"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    result = response.json().get(key)  # unknown ISBN -> {} -> None
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