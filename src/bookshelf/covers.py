"""Find cover images for books that have none.

Most books get a cover straight away from their ISBN (see coverurl.py). This handles the rest,
the ones with no ISBN, by searching Open Library for the title and author. It is polite
(a short pause between searches) and does a few books per call, so the page can show progress.

Command line, handy right after an import:
    python -m bookshelf.covers                      # works through every book without a cover
"""
import argparse
import time
from pathlib import Path

from bookshelf import coverurl
from db import repo
from lookup.openlibrary import search_works
from bookshelf.textnorm import norm, surname

PAUSE = 0.25  # seconds between Open Library searches


def _main_title(title: str) -> str:
    return title.split(":", 1)[0].strip()


def find_cover(book: dict, search=None) -> dict | None:
    """The cover of the Open Library work that matches this book's title (and author, when
    both sides have one), or None. Raises requests.RequestException if Open Library is down."""
    search = search or search_works   # looked up at call time, so tests (and callers) can swap it
    title = book["title"]
    hits = search(title=_main_title(title), author=book["authors"][0] if book["authors"] else None, limit=8)
    wanted, wanted_main = norm(title), norm(_main_title(title))
    surnames = {surname(a) for a in book["authors"]} - {""}
    for hit in hits:
        if not hit.get("cover_url"):
            continue
        if norm(hit["title"]) != wanted and norm(_main_title(hit["title"])) != wanted_main:
            continue
        if surnames and not any(s in norm(a) for s in surnames for a in hit["authors"]):
            continue
        return {"cover_url": hit["cover_url"], "work_key": hit["work_key"]}
    return None


def backfill(conn, limit: int = 10, *, search=None, pause: float = PAUSE) -> dict:
    """Look up covers for up to `limit` books. Every result is saved as it is found, so an
    Open Library outage (requests.RequestException) loses nothing.

    Returns {"checked": n, "found": n, "remaining": books still to look up}."""
    checked = found = 0
    for book in repo.books_missing_cover(conn, limit):
        if book["isbn"]:                                   # no lookup needed
            repo.set_cover(conn, book["id"], coverurl.for_isbn(book["isbn"]))
            found += 1
        else:
            hit = find_cover(book, search)
            if hit:
                repo.set_cover(conn, book["id"], hit["cover_url"], hit["work_key"])
                found += 1
            else:
                repo.mark_cover_checked(conn, book["id"])
            time.sleep(pause)
        checked += 1
    return {"checked": checked, "found": found, "remaining": repo.count_unchecked_missing_covers(conn)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Find covers for books that don't have one.")
    parser.add_argument("--db", default="data/library.db", help="your library database (default: data/library.db)")
    args = parser.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = repo.get_connection(args.db)
    try:
        repo.init_db(conn)
        total = 0
        while True:
            result = backfill(conn)
            total += result["found"]
            print(f"looked at {result['checked']}, found {result['found']}, {result['remaining']} left")
            if not result["checked"] or not result["remaining"]:
                break
        print(f"done: {total} covers found")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
