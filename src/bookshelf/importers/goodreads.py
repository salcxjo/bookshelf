"""Import a Goodreads library export (the CSV from My Books -> Import and export).

Two steps, so nothing changes until you say so:
    plan = plan_import(conn, read_goodreads(path))   # works out what would happen
    apply_plan(conn, plan)                           # does it, in one transaction

What the import does to your library:
  * books already in your library (matched by an earlier import, then ISBN, then title and
    author) keep everything you entered. Goodreads only moves a book *forward*
    (want -> reading -> read), and fills in a finish date, rating, notes, ISBN, publisher,
    year or cover that you left empty.
  * books that aren't in your library yet are created with source = 'import', keeping their
    Goodreads "date added", so "Recently added" still reflects when you actually added them.
  * only the main author is imported. Goodreads' "Additional Authors" mixes translators,
    illustrators and co-authors without saying which is which.

Shelves: read -> Read, currently-reading -> Reading, to-read -> Want to read. By default a
to-read book is treated as a wishlist book you don't own yet.

Command line:
    python -m bookshelf.importers.goodreads goodreads_library_export.csv            # preview only
    python -m bookshelf.importers.goodreads goodreads_library_export.csv --apply
"""
import argparse
import csv
import html
import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from bookshelf.db import repo
from bookshelf.isbn import normalize_isbn
from bookshelf.textnorm import norm

SHELF_STATUS = {"read": "read", "currently-reading": "reading", "to-read": "want"}
STANDARD_SHELVES = set(SHELF_STATUS)
RANK = {"want": 0, "reading": 1, "read": 2}
REQUIRED_COLUMNS = {"Book Id", "Title", "Author", "Exclusive Shelf"}


class GoodreadsFormatError(Exception):
    """The file isn't a Goodreads library export we can read."""


@dataclass
class GoodreadsBook:
    goodreads_id: str
    title: str
    authors: list[str]
    isbn: str | None
    publisher: str | None
    year: int | None
    status: str                  # want / reading / read
    rating: int | None
    finished_on: str | None
    added_on: str | None
    notes: str | None
    owned: bool


@dataclass
class GoodreadsData:
    books: list[GoodreadsBook] = field(default_factory=list)
    rows: int = 0
    skipped_rows: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading the CSV
# --------------------------------------------------------------------------- #

def _unwrap(value: str | None) -> str:
    """Goodreads writes ISBNs as ="9780140449136" so spreadsheets keep leading zeros."""
    value = (value or "").strip()
    if value.startswith('="') and value.endswith('"'):
        value = value[2:-1]
    return value.strip()


def clean_title(title: str) -> str:
    """'Dune (Dune, #1)' -> 'Dune'. Goodreads appends the series to the title."""
    return re.sub(r"\s*\([^()]*#\d+[^()]*\)\s*$", "", title).strip()


def main_title(title: str) -> str:
    """'Walkable City: How Downtown Can Save America' -> 'Walkable City'."""
    return title.split(":", 1)[0].strip()


def _date(value: str | None) -> str | None:
    """'2026/10/22' -> '2026-10-22'."""
    match = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", (value or "").strip())
    return f"{match.group(1)}-{int(match.group(2)):02}-{int(match.group(3)):02}" if match else None


def _rating(value: str | None) -> int | None:
    try:
        stars = int(float(value or 0))
    except ValueError:
        return None
    return stars if 1 <= stars <= 5 else None   # 0 means unrated


def _year(*values: str | None) -> int | None:
    for value in values:
        try:
            year = int(float(value or 0))
        except ValueError:
            continue
        if 0 < year <= 2100:
            return year
    return None


def _notes(review: str | None, private: str | None) -> str | None:
    """Goodreads reviews are HTML-ish (<br/> for line breaks); keep plain text."""
    parts = []
    for text in (review, private):
        text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)[:5000] or None


def read_goodreads(source: str | Path | bytes, *, to_read_is_wishlist: bool = True) -> GoodreadsData:
    """Parse the export. `source` is a path or the raw bytes of an upload.
    Raises GoodreadsFormatError if it isn't a Goodreads export."""
    try:
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise GoodreadsFormatError(f"couldn't read the file as UTF-8 text ({exc})") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise GoodreadsFormatError(
            f"this doesn't look like a Goodreads export (missing columns: {', '.join(sorted(missing))})")

    data = GoodreadsData()
    custom_shelf_books = 0
    for row in reader:
        data.rows += 1
        title = clean_title(row.get("Title") or "")
        shelf = (row.get("Exclusive Shelf") or "").strip()
        if not title or not (row.get("Book Id") or "").strip():
            data.skipped_rows += 1
            continue
        status = SHELF_STATUS.get(shelf)
        if status is None:   # a shelf we don't know: treat like to-read rather than losing the book
            status = "want"

        shelves = {s.strip() for s in (row.get("Bookshelves") or "").split(",") if s.strip()}
        if shelves - STANDARD_SHELVES:
            custom_shelf_books += 1

        owned_copies = _year(row.get("Owned Copies")) or 0   # (any positive whole number)
        author = (row.get("Author") or "").strip()
        data.books.append(GoodreadsBook(
            goodreads_id=row["Book Id"].strip(), title=title, authors=[author] if author else [],
            isbn=normalize_isbn(_unwrap(row.get("ISBN13"))) or normalize_isbn(_unwrap(row.get("ISBN"))),
            publisher=(row.get("Publisher") or "").strip() or None,
            year=_year(row.get("Original Publication Year"), row.get("Year Published")),
            status=status, rating=_rating(row.get("My Rating")),
            finished_on=_date(row.get("Date Read")) if status == "read" else None,
            added_on=_date(row.get("Date Added")),
            notes=_notes(row.get("My Review"), row.get("Private Notes")),
            owned=owned_copies > 0 or status != "want" or not to_read_is_wishlist,
        ))

    if custom_shelf_books:
        data.warnings.append(f"{custom_shelf_books} book(s) are on custom Goodreads shelves; shelves other than "
                             "read, currently-reading and to-read aren't imported.")
    return data


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def _better(a: GoodreadsBook, b: GoodreadsBook) -> GoodreadsBook:
    """Of two rows for the same book, the further-along one (then the rated one)."""
    key = lambda g: (RANK[g.status], g.rating or 0, g.finished_on or "")
    return a if key(a) >= key(b) else b


def _cover(isbn: str | None) -> str | None:
    """Open Library serves covers by ISBN without any lookup; ?default=false makes a missing
    cover a 404, which the page treats as 'no cover' (it shows the title instead)."""
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false" if isbn else None


def _match(index: list[dict], main_titles: dict[int, str], gr: GoodreadsBook, by_gid: dict) -> tuple[int, str] | None:
    if gr.goodreads_id in by_gid:
        return by_gid[gr.goodreads_id], "goodreads_id"
    hit = repo.match_in_index(index, isbn=gr.isbn, title=gr.title, authors=gr.authors)
    if hit:
        return hit
    # Goodreads titles carry subtitles that Open Library keeps separately: compare the main
    # titles, but only when an author is shared, to avoid pairing unrelated books.
    wanted_main = norm(main_title(gr.title))
    wanted_authors = {norm(a) for a in gr.authors}
    if wanted_main:
        for entry in index:
            if main_titles[entry["id"]] == wanted_main and entry["authors"] & wanted_authors:
                return entry["id"], "title"
    return None


def _changes(current: dict, gr: GoodreadsBook) -> tuple[dict, dict]:
    """(shelf changes, book changes) for an existing book. Never downgrades, never
    overwrites anything you already have."""
    shelf: dict = {}
    shelved = current["status"]
    if shelved is None or RANK[gr.status] > RANK[shelved]:
        shelf["status"] = gr.status
    final = shelf.get("status", shelved)
    if final == "read" and gr.finished_on and not current["finished_on"]:
        shelf["finished_on"] = gr.finished_on
    if gr.rating and current["rating"] is None:
        shelf["rating"] = gr.rating
    if gr.notes and not current["notes"]:
        shelf["notes"] = gr.notes

    book: dict = {}
    for key in ("publisher", "year"):
        if getattr(gr, key) is not None and current[key] is None:
            book[key] = getattr(gr, key)
    return shelf, book


def plan_import(conn: sqlite3.Connection, data: GoodreadsData) -> dict:
    """Work out what an import would do, without touching the database.

    Returns {"entries": [...internal...], "report": {...JSON-friendly summary...}}.
    """
    index = repo.library_index(conn)
    main_titles = {r["id"]: norm(main_title(r["title"])) for r in conn.execute("SELECT id, title FROM books")}
    by_gid = {r["goodreads_id"]: r["id"] for r in conn.execute("SELECT id, goodreads_id FROM books WHERE goodreads_id IS NOT NULL")}
    taken_isbns = {e["isbn"] for e in index if e["isbn"]}

    entries: dict = {}
    for gr in data.books:
        hit = _match(index, main_titles, gr, by_gid)
        book_id, match = hit if hit else (None, None)
        key = ("book", book_id) if book_id else ("new", norm(gr.title) + "|" + norm(gr.authors[0] if gr.authors else ""))
        entry = entries.get(key)
        if entry is None:
            entries[key] = {"book_id": book_id, "match": match, "gr": gr, "merged": 0}
        else:                              # the same book twice in the export
            entry["gr"] = _better(entry["gr"], gr)
            entry["merged"] += 1

    items, counts = [], {"create": 0, "update": 0, "unchanged": 0}
    for entry in entries.values():
        gr = entry["gr"]
        if entry["book_id"]:
            current = repo.get_book_by_id(conn, entry["book_id"])
            entry["shelf"], entry["book"] = _changes(current, gr)
            if current["isbn"] is None and gr.isbn and gr.isbn not in taken_isbns:
                entry["book"]["isbn"] = gr.isbn
                taken_isbns.add(gr.isbn)
                if current["cover_url"] is None:
                    entry["book"]["cover_url"] = _cover(gr.isbn)
            entry["link"] = current.get("goodreads_id") is None
            entry["action"] = "update" if (entry["shelf"] or entry["book"] or entry["link"]) else "unchanged"
            title, authors = current["title"], current["authors"]
            described = [f"{k.replace('_', ' ')}: {current.get(k) if current.get(k) not in (None, '') else '—'} → {v}"
                         for k, v in {**entry["shelf"], **entry["book"]}.items() if k not in ("notes", "cover_url")]
            if "notes" in entry["shelf"]:
                described.append("notes: added from your review")
        else:
            isbn = gr.isbn if gr.isbn and gr.isbn not in taken_isbns else None
            if isbn:
                taken_isbns.add(isbn)
            entry["isbn"] = isbn
            entry["action"] = "create"
            title, authors = gr.title, gr.authors
            described = [gr.status + (f" · {gr.rating}★" if gr.rating else "")
                         + (f" · finished {gr.finished_on}" if gr.finished_on else "")
                         + ("" if gr.owned else " · wishlist")]
        counts[entry["action"]] += 1
        items.append({"title": title, "authors": authors, "action": entry["action"], "match": entry["match"],
                      "goodreads_status": gr.status, "changes": described})

    report = {
        "applied": False,
        "counts": {**counts, "duplicates_merged": sum(e["merged"] for e in entries.values()),
                   "rows": data.rows, "skipped_rows": data.skipped_rows},
        "items": items,
        "warnings": list(data.warnings),
    }
    return {"entries": list(entries.values()), "report": report}


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #

def apply_plan(conn: sqlite3.Connection, plan: dict) -> dict:
    """Carry out a plan from plan_import, all or nothing. Returns the report, marked applied."""
    with conn:
        for entry in plan["entries"]:
            gr, action = entry["gr"], entry["action"]
            if action == "unchanged":
                continue

            if action == "create":
                added_at = f"{gr.added_on} 00:00:00" if gr.added_on else None
                cursor = conn.execute(
                    "INSERT INTO books(isbn, title, publisher, year, cover_url, source, owned, goodreads_id, added_at) "
                    "VALUES (?, ?, ?, ?, ?, 'import', ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                    (entry["isbn"], gr.title, gr.publisher, gr.year, _cover(entry["isbn"]), int(gr.owned),
                     gr.goodreads_id, added_at),
                )
                book_id = cursor.lastrowid
                repo.link_authors(conn, book_id, repo.clean_names(gr.authors))
                conn.execute(
                    "INSERT INTO shelf(book_id, status, rating, finished_on, notes) VALUES (?, ?, ?, ?, ?)",
                    (book_id, gr.status, gr.rating, gr.finished_on, gr.notes),
                )
            else:
                book_id = entry["book_id"]
                if entry["link"]:
                    conn.execute("UPDATE books SET goodreads_id = ? WHERE id = ?", (gr.goodreads_id, book_id))
                if entry["book"]:
                    assignments = ", ".join(f"{c} = ?" for c in entry["book"])   # keys come from _changes / plan_import
                    conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", (*entry["book"].values(), book_id))
                if entry["shelf"]:
                    conn.execute("INSERT OR IGNORE INTO shelf(book_id, status) VALUES (?, 'want')", (book_id,))
                    assignments = ", ".join(f"{c} = ?" for c in entry["shelf"])
                    conn.execute(f"UPDATE shelf SET {assignments} WHERE book_id = ?", (*entry["shelf"].values(), book_id))
    return {**plan["report"], "applied": True}


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def format_report(report: dict) -> str:
    c = report["counts"]
    lines = [
        f"{'APPLIED' if report['applied'] else 'PREVIEW (nothing has been changed)'}",
        f"  rows in the export: {c['rows']}  (skipped, no title: {c['skipped_rows']})",
        f"  new: {c['create']}   updated: {c['update']}   unchanged: {c['unchanged']}   duplicates merged: {c['duplicates_merged']}",
    ]
    for item in report["items"]:
        if item["action"] != "unchanged":
            lines.append(f"  [{item['action']:6}] {item['title']} — {', '.join(item['authors']) or 'unknown author'}"
                         + (f"  ({'; '.join(item['changes'])})" if item["changes"] else ""))
    lines += [f"  ! {w}" for w in report["warnings"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import a Goodreads library export (CSV).")
    parser.add_argument("csv_file", help="goodreads_library_export.csv")
    parser.add_argument("--db", default="data/library.db", help="your library database (default: data/library.db)")
    parser.add_argument("--apply", action="store_true", help="really import; without this it only previews")
    parser.add_argument("--owned-to-read", action="store_true",
                        help="count to-read books as owned instead of as a wishlist")
    args = parser.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = repo.get_connection(args.db)
    try:
        repo.init_db(conn)
        plan = plan_import(conn, read_goodreads(args.csv_file, to_read_is_wishlist=not args.owned_to_read))
        print(format_report(apply_plan(conn, plan) if args.apply else plan["report"]))
    except GoodreadsFormatError as exc:
        raise SystemExit(f"error: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
