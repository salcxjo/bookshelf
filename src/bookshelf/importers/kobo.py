"""Import reading state and highlights from a Kobo e-reader's KoboReader.sqlite.

The file lives at .kobo/KoboReader.sqlite on the device (a hidden folder). It is only ever
opened read-only here; nothing is written back to it.

Two steps, so nothing changes until you say so:
    plan  = plan_import(conn, read_kobo(path))     # works out what would happen
    apply_plan(conn, plan)                         # does it, in one transaction

What the import does to your library:
  * books already in your library (matched by an earlier import, then ISBN, then title and
    author) keep everything you entered. Kobo only moves a book *forward* (want -> reading ->
    read), fills in dates and ratings you left empty, and records reading progress.
  * books that aren't in your library yet are created with source = 'kobo'.
  * highlights and notes are added once each (re-importing never duplicates them).

Command line:
    python -m bookshelf.importers.kobo /path/to/KoboReader.sqlite            # preview only
    python -m bookshelf.importers.kobo /path/to/KoboReader.sqlite --apply
"""
import argparse
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from db import repo
from bookshelf.isbn import normalize_isbn
from bookshelf.textnorm import norm

KOBO_STATUS = {0: "want", 1: "reading", 2: "read"}   # content.ReadStatus
RANK = {"want": 0, "reading": 1, "read": 2}


class KoboFormatError(Exception):
    """The file isn't a KoboReader.sqlite we can read."""


@dataclass
class KoboBook:
    content_id: str
    title: str
    authors: list[str]
    isbn: str | None
    publisher: str | None
    read_status: int            # 0 unread, 1 reading, 2 finished
    percent_read: int | None
    seconds_read: int | None
    last_read_on: str | None
    started_on: str | None
    finished_on: str | None
    rating: int | None = None


@dataclass
class KoboHighlight:
    bookmark_id: str
    volume_id: str              # ContentID of the book it belongs to
    text: str | None
    note: str | None
    chapter: str | None
    created_on: str | None


@dataclass
class KoboData:
    books: list[KoboBook] = field(default_factory=list)
    highlights: list[KoboHighlight] = field(default_factory=list)
    skipped_unread: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading the Kobo database
# --------------------------------------------------------------------------- #

def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _date(value) -> str | None:
    """'2025-08-14T10:00:00Z' -> '2025-08-14'; anything else -> None."""
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else None
    return value or None


def _authors(attribution) -> list[str]:
    parts = re.split(r"\s*(?:&|;)\s*", attribution or "")
    return [p.strip() for p in parts if p.strip()]


def read_kobo(path: str | Path, *, include_unread: bool = False) -> KoboData:
    """Read books, progress and highlights. Raises KoboFormatError for anything else."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise KoboFormatError(f"couldn't open the file: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        return _read(conn, include_unread)
    except sqlite3.DatabaseError as exc:  # "file is not a database", corrupt files...
        raise KoboFormatError(f"this doesn't look like a KoboReader.sqlite ({exc})") from exc
    finally:
        conn.close()


def _read(conn: sqlite3.Connection, include_unread: bool) -> KoboData:
    data = KoboData()

    content = _columns(conn, "content")
    if not {"ContentID", "ContentType", "Title"} <= content:
        raise KoboFormatError("no usable 'content' table; is this really KoboReader.sqlite?")

    def pick(name: str) -> str:
        return f'"{name}"' if name in content else f'NULL AS "{name}"'

    wanted = ["ContentID", "Title", "Attribution", "ISBN", "Publisher", "ReadStatus", "___PercentRead",
              "DateLastRead", "TimeSpentReading", "LastTimeStartedReading", "LastTimeFinishedReading", "IsDownloaded"]
    sql = f"SELECT {', '.join(pick(c) for c in wanted)} FROM content WHERE ContentType = 6"
    if "BookID" in content:
        sql += " AND BookID IS NULL"   # chapters point at their book through BookID; books don't

    ratings: dict[str, int] = {}
    if "ratings" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
        rcols = {c.lower() for c in _columns(conn, "ratings")}
        if {"contentid", "rating"} <= rcols:
            for row in conn.execute("SELECT ContentID, rating FROM ratings"):
                stars = _int(row[1])
                if stars and 1 <= stars <= 5:
                    ratings[row[0]] = stars

    if include_unread and "IsDownloaded" not in content:
        data.warnings.append("This database has no IsDownloaded column, so store previews and recommendations "
                             "can't be told apart from your own unopened books.")

    for row in conn.execute(sql):
        title = _text(row["Title"])
        if not title:
            continue
        status = _int(row["ReadStatus"]) or 0
        if status not in KOBO_STATUS:
            status = 0
        if status == 0:
            downloaded = str(row["IsDownloaded"]).lower() in ("true", "1") if row["IsDownloaded"] is not None else True
            if not include_unread or not downloaded:
                data.skipped_unread += 1
                continue

        percent = _int(row["___PercentRead"])
        percent = max(0, min(100, percent)) if percent is not None else None
        if status == 2:
            percent = 100            # Kobo stores 0% for finished books
        last_read = _date(row["DateLastRead"])
        data.books.append(KoboBook(
            content_id=row["ContentID"], title=title, authors=_authors(row["Attribution"]),
            isbn=normalize_isbn(row["ISBN"]), publisher=_text(row["Publisher"]), read_status=status,
            percent_read=percent, seconds_read=_int(row["TimeSpentReading"]), last_read_on=last_read,
            started_on=_date(row["LastTimeStartedReading"]),
            finished_on=(_date(row["LastTimeFinishedReading"]) or last_read) if status == 2 else None,
            rating=ratings.get(row["ContentID"]),
        ))

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "Bookmark" not in tables:
        data.warnings.append("No Bookmark table found, so highlights were skipped.")
    else:
        b = _columns(conn, "Bookmark")
        if not {"BookmarkID", "VolumeID"} <= b:
            data.warnings.append("The Bookmark table has an unexpected layout, so highlights were skipped.")
        else:
            text = "b.Text" if "Text" in b else "NULL"
            note = "b.Annotation" if "Annotation" in b else "NULL"
            created = "b.DateCreated" if "DateCreated" in b else "NULL"
            chapter = "ch.Title" if "ContentID" in b else "NULL"
            join = "LEFT JOIN content ch ON ch.ContentID = b.ContentID" if "ContentID" in b else ""
            where = f"(COALESCE({text}, '') <> '' OR COALESCE({note}, '') <> '')"
            if "Type" in b:
                where += " AND COALESCE(b.Type, '') <> 'dogear'"   # dog-ears are bookmarks without text
            rows = conn.execute(
                f"SELECT b.BookmarkID, b.VolumeID, {text} AS text, {note} AS note, {created} AS created, "
                f"{chapter} AS chapter FROM Bookmark b {join} WHERE {where} ORDER BY b.DateCreated"
                if "DateCreated" in b else
                f"SELECT b.BookmarkID, b.VolumeID, {text} AS text, {note} AS note, {created} AS created, "
                f"{chapter} AS chapter FROM Bookmark b {join} WHERE {where}"
            )
            for row in rows:
                data.highlights.append(KoboHighlight(
                    bookmark_id=row["BookmarkID"], volume_id=row["VolumeID"], text=_text(row["text"]),
                    note=_text(row["note"]), chapter=_text(row["chapter"]), created_on=_date(row["created"]),
                ))
    return data


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def _better(a: KoboBook, b: KoboBook) -> KoboBook:
    """Of two Kobo rows for the same book (e.g. sideloaded + store copy), the further-along one."""
    key = lambda k: (k.read_status, k.percent_read or 0, k.seconds_read or 0)
    return a if key(a) >= key(b) else b


def _shelf_changes(current: dict, kobo: KoboBook) -> dict:
    """What Kobo would change on an existing book. Never downgrades, never overwrites
    dates or ratings you already have."""
    changes: dict = {}
    target = KOBO_STATUS[kobo.read_status]
    shelved = current["status"]
    if shelved is None or RANK[target] > RANK[shelved]:
        changes["status"] = target
    final = changes.get("status", shelved)

    if kobo.started_on and not current["started_on"] and final in ("reading", "read"):
        changes["started_on"] = kobo.started_on
    if final == "read" and kobo.finished_on and not current["finished_on"]:
        changes["finished_on"] = kobo.finished_on
    if kobo.rating and current["rating"] is None:
        changes["rating"] = kobo.rating
    wanted = {"percent_read": 100 if final == "read" else kobo.percent_read,
              "seconds_read": kobo.seconds_read, "last_read_on": kobo.last_read_on}
    for field_name, value in wanted.items():
        if value is not None and value != current.get(field_name):
            changes[field_name] = value
    return changes


def plan_import(conn: sqlite3.Connection, data: KoboData) -> dict:
    """Work out what an import would do, without touching the database.

    Returns {"entries": [...internal...], "report": {...JSON-friendly summary...}}.
    """
    index = repo.library_index(conn)
    by_kobo_id = {r["kobo_id"]: r["id"] for r in conn.execute("SELECT id, kobo_id FROM books WHERE kobo_id IS NOT NULL")}
    known_bookmarks = {r[0] for r in conn.execute("SELECT kobo_bookmark_id FROM highlights WHERE kobo_bookmark_id IS NOT NULL")}
    taken_isbns = {e["isbn"] for e in index if e["isbn"]}

    entries: dict = {}          # one per distinct book: ("book", id) for existing, ("new", key) for new
    by_content_id: dict = {}
    for kobo in data.books:
        book_id, match = None, None
        if kobo.content_id in by_kobo_id:
            book_id, match = by_kobo_id[kobo.content_id], "kobo_id"
        else:
            hit = repo.match_in_index(index, isbn=kobo.isbn, title=kobo.title, authors=kobo.authors)
            if hit:
                book_id, match = hit
        key = ("book", book_id) if book_id else ("new", norm(kobo.title) + "|" + norm(kobo.authors[0] if kobo.authors else ""))

        entry = entries.get(key)
        if entry is None:
            entry = entries[key] = {"book_id": book_id, "match": match, "kobo": kobo, "content_ids": [kobo.content_id],
                                    "merged": 0, "highlights": []}
        else:                   # the same book twice in the Kobo database
            entry["kobo"] = _better(entry["kobo"], kobo)
            entry["content_ids"].append(kobo.content_id)
            entry["merged"] += 1
        by_content_id[kobo.content_id] = entry

    highlights_skipped = 0
    seen_bookmarks = set()
    for h in data.highlights:
        entry = by_content_id.get(h.volume_id)
        if entry is None:
            highlights_skipped += 1
        elif h.bookmark_id not in known_bookmarks and h.bookmark_id not in seen_bookmarks:
            seen_bookmarks.add(h.bookmark_id)
            entry["highlights"].append(h)

    items, counts = [], {"create": 0, "update": 0, "unchanged": 0}
    for entry in entries.values():
        kobo = entry["kobo"]
        if entry["book_id"]:
            current = repo.get_book_by_id(conn, entry["book_id"])
            entry["changes"] = _shelf_changes(current, kobo)
            entry["link"] = current.get("kobo_id") is None and entry["match"] != "kobo_id"
            entry["action"] = "update" if (entry["changes"] or entry["link"] or entry["highlights"]) else "unchanged"
            title, authors = current["title"], current["authors"]
            described = [f"{k.replace('_', ' ')}: {current.get(k) if current.get(k) is not None else '—'} → {v}"
                         for k, v in entry["changes"].items() if k not in ("seconds_read", "last_read_on")]
        else:
            isbn = kobo.isbn if kobo.isbn and kobo.isbn not in taken_isbns else None
            if isbn:
                taken_isbns.add(isbn)
            entry["isbn"] = isbn
            entry["action"] = "create"
            title, authors, described = kobo.title, kobo.authors, [f"new, {KOBO_STATUS[kobo.read_status]}"]
        counts[entry["action"]] += 1
        items.append({"title": title, "authors": authors, "action": entry["action"], "match": entry["match"],
                      "kobo_status": KOBO_STATUS[kobo.read_status], "percent_read": kobo.percent_read,
                      "changes": described, "highlights": len(entry["highlights"])})

    report = {
        "applied": False,
        "counts": {**counts, "duplicates_merged": sum(e["merged"] for e in entries.values()),
                   "skipped_unread": data.skipped_unread, "kobo_books": len(data.books),
                   "highlights_new": sum(len(e["highlights"]) for e in entries.values()),
                   "highlights_skipped": highlights_skipped},
        "items": items,
        "warnings": list(data.warnings),
    }
    return {"entries": list(entries.values()), "report": report}


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #

SHELF_COLUMNS = ("status", "started_on", "finished_on", "rating", "percent_read", "seconds_read", "last_read_on")


def apply_plan(conn: sqlite3.Connection, plan: dict) -> dict:
    """Carry out a plan from plan_import, all or nothing. Returns the report, marked applied."""
    with conn:
        for entry in plan["entries"]:
            kobo, action = entry["kobo"], entry["action"]
            if action == "unchanged":
                continue

            if action == "create":
                cursor = conn.execute(
                    "INSERT INTO books(isbn, title, publisher, source, owned, kobo_id) VALUES (?, ?, ?, 'kobo', 1, ?)",
                    (entry["isbn"], kobo.title, kobo.publisher, kobo.content_id),
                )
                book_id = cursor.lastrowid
                repo.link_authors(conn, book_id, repo.clean_names(kobo.authors))
                shelf = {"status": KOBO_STATUS[kobo.read_status], "started_on": kobo.started_on,
                         "finished_on": kobo.finished_on, "rating": kobo.rating, "percent_read": kobo.percent_read,
                         "seconds_read": kobo.seconds_read, "last_read_on": kobo.last_read_on}
                conn.execute(
                    f"INSERT INTO shelf(book_id, {', '.join(SHELF_COLUMNS)}) VALUES (?, {', '.join('?' * len(SHELF_COLUMNS))})",
                    (book_id, *(shelf[c] for c in SHELF_COLUMNS)),
                )
            else:
                book_id = entry["book_id"]
                if entry["link"]:
                    conn.execute("UPDATE books SET kobo_id = ? WHERE id = ?", (kobo.content_id, book_id))
                if entry["changes"]:
                    conn.execute("INSERT OR IGNORE INTO shelf(book_id, status) VALUES (?, 'want')", (book_id,))
                    assignments = ", ".join(f"{c} = ?" for c in entry["changes"])   # keys come from _shelf_changes
                    conn.execute(f"UPDATE shelf SET {assignments} WHERE book_id = ?", (*entry["changes"].values(), book_id))

            for h in entry["highlights"]:
                conn.execute(
                    "INSERT INTO highlights(book_id, kobo_bookmark_id, text, note, chapter, created_on) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kobo_bookmark_id) DO NOTHING",
                    (book_id, h.bookmark_id, h.text, h.note, h.chapter, h.created_on),
                )
    return {**plan["report"], "applied": True}


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def format_report(report: dict) -> str:
    c = report["counts"]
    lines = [
        f"{'APPLIED' if report['applied'] else 'PREVIEW (nothing has been changed)'}",
        f"  Kobo books considered: {c['kobo_books']}  (unread books skipped: {c['skipped_unread']})",
        f"  new: {c['create']}   updated: {c['update']}   unchanged: {c['unchanged']}   duplicates merged: {c['duplicates_merged']}",
        f"  highlights to add: {c['highlights_new']}   (for books not imported: {c['highlights_skipped']})",
    ]
    for item in report["items"]:
        if item["action"] != "unchanged":
            lines.append(f"  [{item['action']:6}] {item['title']} — {', '.join(item['authors']) or 'unknown author'}"
                         + (f"  ({'; '.join(item['changes'])})" if item["changes"] else ""))
    lines += [f"  ! {w}" for w in report["warnings"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import a Kobo e-reader's library, progress and highlights.")
    parser.add_argument("kobo_db", help="path to KoboReader.sqlite (the .kobo folder on the device)")
    parser.add_argument("--db", default="data/library.db", help="your library database (default: data/library.db)")
    parser.add_argument("--apply", action="store_true", help="really import; without this it only previews")
    parser.add_argument("--include-unread", action="store_true", help="also import downloaded books you haven't opened")
    args = parser.parse_args(argv)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = repo.get_connection(args.db)
    try:
        repo.init_db(conn)
        plan = plan_import(conn, read_kobo(args.kobo_db, include_unread=args.include_unread))
        print(format_report(apply_plan(conn, plan) if args.apply else plan["report"]))
    except KoboFormatError as exc:
        raise SystemExit(f"error: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
