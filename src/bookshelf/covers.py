"""Fill in missing ISBNs and covers for books, using Open Library's search.

Books that came from a Kobo or were typed in by hand often have no ISBN. For each of them this
searches Open Library by title and author and, when it finds the same book, fills in:
  * the ISBN (which also makes later cover and duplicate checks easier), and
  * the cover (the one Open Library shows for the book, else one built from the new ISBN).
Only blanks are filled, and nothing is overwritten. A book is looked up once; if nothing is
found it isn't asked about again.

The ISBN comes from one edition of the book, so it may not be the exact edition you own.

It is polite (a short pause between searches) and does a few books per call, so the page can
show progress. Command line, handy right after an import:
    python -m bookshelf.covers                      # works through every book that needs it
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


def find_match(book: dict, search=None) -> dict | None:
    """The Open Library work that is this book (same title, and a shared author when both sides
    have one), preferring one that has both an ISBN and a cover. Raises requests.RequestException
    if Open Library is down."""
    search = search or search_works   # looked up at call time, so tests (and callers) can swap it
    title = book["title"]
    hits = search(title=_main_title(title), author=book["authors"][0] if book["authors"] else None, limit=8)
    wanted, wanted_main = norm(title), norm(_main_title(title))
    surnames = {surname(a) for a in book["authors"]} - {""}

    matching = []
    for hit in hits:
        if norm(hit["title"]) != wanted and norm(_main_title(hit["title"])) != wanted_main:
            continue
        if surnames and not any(s in norm(a) for s in surnames for a in hit["authors"]):
            continue
        matching.append(hit)
    matching.sort(key=lambda h: bool(h.get("isbn")) + bool(h.get("cover_url")), reverse=True)  # stable: keeps search order among equals
    return matching[0] if matching else None


def backfill(conn, limit: int = 10, *, search=None, pause: float = PAUSE) -> dict:
    """Look up up to `limit` books. Every result is saved as it is found, so an Open Library
    outage (requests.RequestException) loses nothing.

    Returns {"checked": books looked at, "found": books improved, "isbns": ISBNs added,
             "covers": covers added, "remaining": books still to look up}."""
    checked = found = isbns = covers_added = 0
    for book in repo.books_to_look_up(conn, limit):
        checked += 1
        if book["isbn"]:                                   # has an ISBN, only a cover is missing: no search needed
            repo.set_cover(conn, book["id"], coverurl.for_isbn(book["isbn"]))
            found += 1
            covers_added += 1
            continue

        hit = find_match(book, search)
        time.sleep(pause)
        isbn = hit["isbn"] if hit and hit["isbn"] and not repo.isbn_in_use(conn, hit["isbn"]) else None
        cover = (hit["cover_url"] or coverurl.for_isbn(isbn)) if hit and book["cover_url"] is None else None
        saved = repo.apply_lookup(conn, book["id"], isbn=isbn, cover_url=cover, work_key=hit["work_key"] if hit else None)
        isbns += saved["isbn"]
        covers_added += saved["cover"]
        found += bool(saved["isbn"] or saved["cover"])
    return {"checked": checked, "found": found, "isbns": isbns, "covers": covers_added,
            "remaining": repo.count_to_look_up(conn)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fill in missing ISBNs and covers using Open Library.")
    parser.add_argument("--db", default="data/library.db", help="your library database (default: data/library.db)")
    args = parser.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = repo.get_connection(args.db)
    try:
        repo.init_db(conn)
        isbns = covers_added = 0
        while True:
            result = backfill(conn)
            isbns += result["isbns"]
            covers_added += result["covers"]
            print(f"looked at {result['checked']}: +{result['isbns']} ISBNs, +{result['covers']} covers, {result['remaining']} left")
            if not result["checked"] or not result["remaining"]:
                break
        print(f"done: {isbns} ISBNs and {covers_added} covers added")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
