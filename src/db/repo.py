"""All SQL lives here. Every function takes an open connection as its first argument."""
import json
import sqlite3
from pathlib import Path

from bookshelf.db.migrate import migrate
from bookshelf.textnorm import norm

STATUSES = ("want", "reading", "read")
BOOK_FIELDS = {"title", "subtitle", "authors", "publisher", "year", "cover_url", "owned"}
SHELF_FIELDS = {"status", "rating", "notes"}


class DuplicateBookError(Exception):
    """Raised when a book with the same ISBN already exists."""


def get_connection(path: str | Path) -> sqlite3.Connection:
    """Open a connection with dict-like rows and foreign keys enforced.

    Open one per request (see get_db in main.py), not one global connection.
    check_same_thread=False is safe for that pattern because FastAPI may run a
    dependency and its route in different worker threads, but a single
    connection is only ever used by one request at a time.
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Bring the database up to the latest schema (see db/migrate.py). Safe to repeat."""
    migrate(conn)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

# One query shape for every read, so get/list always return the same dict:
# the book's columns, its shelf columns (None if unshelved) and `authors` as a
# list in the order they were inserted (ba.rowid). `description` is only included on
# request: synopses are long, so the list endpoint leaves them out.
_SELECT = """
    SELECT b.id, b.isbn, b.title, b.subtitle, b.publisher, b.year, b.cover_url,
           {description}
           b.work_key, b.source, b.owned,
           s.status, s.rating, s.started_on, s.finished_on, s.notes,
           (SELECT json_group_array(name) FROM (
                SELECT a.name AS name
                FROM book_authors ba
                JOIN authors a ON a.id = ba.author_id
                WHERE ba.book_id = b.id
                ORDER BY ba.rowid
           )) AS authors
    FROM books b
    LEFT JOIN shelf s ON s.book_id = b.id
"""


def _book_select(include_description: bool = False) -> str:
    return _SELECT.format(description="b.description," if include_description else "")


def _to_book(row: sqlite3.Row) -> dict:
    book = dict(row)
    book["authors"] = json.loads(book["authors"])
    book["owned"] = bool(book["owned"])
    return book


def get_book_by_isbn(conn: sqlite3.Connection, isbn: str) -> dict | None:
    row = conn.execute(_book_select() + " WHERE b.isbn = ?", (isbn,)).fetchone()
    return _to_book(row) if row else None


def get_book_by_id(conn: sqlite3.Connection, book_id: int, *, include_description: bool = False) -> dict | None:
    row = conn.execute(_book_select(include_description) + " WHERE b.id = ?", (book_id,)).fetchone()
    return _to_book(row) if row else None


def list_books(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """All books, newest first, optionally only those with one shelf status."""
    sql = _book_select()
    params: tuple = ()
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        sql += " WHERE s.status = ?"
        params = (status,)
    sql += " ORDER BY b.added_at DESC, b.id DESC"
    return [_to_book(row) for row in conn.execute(sql, params)]


# --------------------------------------------------------------------------- #
# Duplicate detection
# --------------------------------------------------------------------------- #

def library_index(conn: sqlite3.Connection) -> list[dict]:
    """A light snapshot of the library for matching: ids, ISBNs, work keys and normalised
    titles/authors. A personal library is small, so matching in Python is fine."""
    rows = conn.execute(
        """
        SELECT b.id, b.isbn, b.work_key, b.title,
               (SELECT json_group_array(a.name)
                FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                WHERE ba.book_id = b.id) AS authors
        FROM books b
        """
    ).fetchall()
    return [
        {"id": r["id"], "isbn": r["isbn"], "work_key": r["work_key"], "title": norm(r["title"]),
         "authors": {norm(a) for a in json.loads(r["authors"])}}
        for r in rows
    ]


def match_in_index(index: list[dict], *, isbn=None, work_key=None, title=None, authors=()) -> tuple[int, str] | None:
    """Find a book that is probably the same one. Returns (book_id, reason) or None.

    Reasons, strongest first: 'isbn' (same edition), 'work' (same Open Library work, maybe a
    different edition), 'title' (same title and an author in common, or one side has no authors).
    """
    if isbn:
        for entry in index:
            if entry["isbn"] == isbn:
                return entry["id"], "isbn"
    if work_key:
        for entry in index:
            if entry["work_key"] == work_key:
                return entry["id"], "work"
    wanted_title = norm(title)
    wanted_authors = {norm(a) for a in authors if a}
    if wanted_title:
        for entry in index:
            if entry["title"] == wanted_title and (
                not wanted_authors or not entry["authors"] or wanted_authors & entry["authors"]
            ):
                return entry["id"], "title"
    return None


def find_duplicate(conn: sqlite3.Connection, *, isbn=None, work_key=None, title=None, authors=()) -> tuple[dict, str] | None:
    """The existing book (full dict) that matches, with the reason, or None."""
    hit = match_in_index(library_index(conn), isbn=isbn, work_key=work_key, title=title, authors=authors)
    if hit is None:
        return None
    return get_book_by_id(conn, hit[0]), hit[1]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def _clean_text(value) -> str | None:
    """Strip a text field; blank becomes NULL."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value.strip() or None


def _clean_names(names) -> list[str]:
    """Strip author names, drop blanks and repeats, keep the order."""
    if not isinstance(names, (list, tuple)):
        raise ValueError("authors must be a list of names")
    seen, result = set(), []
    for name in names:
        name = _clean_text(name)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def _link_authors(conn: sqlite3.Connection, book_id: int, names: list[str]) -> None:
    for name in names:
        conn.execute("INSERT OR IGNORE INTO authors(name) VALUES (?)", (name,))
        author_id = conn.execute("SELECT id FROM authors WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO book_authors(book_id, author_id) VALUES (?, ?)",
            (book_id, author_id),
        )


def _drop_orphan_authors(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM authors WHERE id NOT IN (SELECT author_id FROM book_authors)")


def add_book(conn: sqlite3.Connection, book: dict) -> int:
    """Insert a book, its authors, and the links between them in one transaction.

    Text fields are stripped (blank becomes NULL). Returns the new book's id.
    Raises DuplicateBookError if the ISBN exists, ValueError if the title is blank.
    """
    title = _clean_text(book.get("title"))
    if title is None:
        raise ValueError("title cannot be empty")

    # `with conn:` commits on success and rolls everything back on any exception
    with conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO books(isbn, title, subtitle, publisher, year, cover_url,
                                  description, work_key, source, owned)
                VALUES (:isbn, :title, :subtitle, :publisher, :year, :cover_url,
                        :description, :work_key, :source, :owned)
                """,
                {
                    "isbn": book.get("isbn"),
                    "title": title,
                    "subtitle": _clean_text(book.get("subtitle")),
                    "publisher": _clean_text(book.get("publisher")),
                    "year": book.get("year"),
                    "cover_url": _clean_text(book.get("cover_url")),
                    "description": book.get("description"),
                    "work_key": book.get("work_key"),
                    "source": book.get("source", "scan"),
                    "owned": int(book.get("owned", True)),
                },
            )
        except sqlite3.IntegrityError as exc:
            # Only translate the duplicate-ISBN case; other violations (e.g. a
            # missing title) should surface as the real error.
            if "books.isbn" in str(exc):
                raise DuplicateBookError(book.get("isbn")) from exc
            raise
        book_id = cursor.lastrowid
        _link_authors(conn, book_id, _clean_names(book.get("authors", [])))

    return book_id


def set_description(conn: sqlite3.Connection, book_id: int, description: str, work_key: str | None = None) -> None:
    """Cache a synopsis. '' means 'looked it up, there isn't one'."""
    with conn:
        conn.execute(
            "UPDATE books SET description = ?, work_key = COALESCE(?, work_key) WHERE id = ?",
            (description, work_key, book_id),
        )


def update_book(conn: sqlite3.Connection, book_id: int, changes: dict) -> bool:
    """Edit a book's own details. Returns False if the book doesn't exist.

    Allowed keys: title, subtitle, authors (replaces the whole list), publisher, year,
    cover_url, owned. Blank text becomes NULL, except title which must not be blank.
    Raises ValueError for unknown keys or invalid values.
    """
    unknown = set(changes) - BOOK_FIELDS
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    columns: dict = {}
    if "title" in changes:
        columns["title"] = _clean_text(changes["title"])
        if columns["title"] is None:
            raise ValueError("title cannot be empty")
    for key in ("subtitle", "publisher"):
        if key in changes:
            columns[key] = _clean_text(changes[key])
    if "cover_url" in changes:
        url = _clean_text(changes["cover_url"])
        if url is not None and not url.lower().startswith(("http://", "https://")):
            raise ValueError("cover_url must start with http:// or https://")
        columns["cover_url"] = url
    if "year" in changes:
        year = changes["year"]
        if year is not None and not (isinstance(year, int) and not isinstance(year, bool) and 0 <= year <= 2100):
            raise ValueError("year must be a whole number between 0 and 2100, or null")
        columns["year"] = year
    if "owned" in changes:
        if not isinstance(changes["owned"], bool):
            raise ValueError("owned must be true or false")
        columns["owned"] = int(changes["owned"])
    names = _clean_names(changes["authors"]) if "authors" in changes else None

    with conn:
        if conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            return False
        if columns:
            assignments = ", ".join(f"{column} = ?" for column in columns)  # keys come from the whitelist above
            conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", (*columns.values(), book_id))
        if names is not None:
            conn.execute("DELETE FROM book_authors WHERE book_id = ?", (book_id,))
            _link_authors(conn, book_id, names)
            _drop_orphan_authors(conn)
    return True


def set_status(conn: sqlite3.Connection, book_id: int, status: str) -> None:
    """Create or update the shelf row for a book, without touching any dates.

    Used by /scan for the initial status. For user-driven changes (which also
    maintain started_on / finished_on) use update_shelf.

    Raises ValueError for an unknown status. An unknown book_id raises
    sqlite3.IntegrityError (foreign key).
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")

    with conn:
        conn.execute(
            """
            INSERT INTO shelf(book_id, status)
            VALUES (?, ?)
            ON CONFLICT(book_id) DO UPDATE SET status = excluded.status
            """,
            (book_id, status),
        )


def update_shelf(conn: sqlite3.Connection, book_id: int, changes: dict) -> bool:
    """Apply user changes to a book's shelf row. Returns False if the book doesn't exist.

    `changes` may contain "status", "rating" (None clears it) and "notes" (blank clears it).
    Status changes keep the dates in step, using the server's local date:
      -> reading: started_on is set if empty, finished_on is cleared
      -> read:    finished_on is set if empty (started_on is left alone)
      -> want:    both dates are cleared
    Raises ValueError for unknown fields or invalid values.
    """
    unknown = set(changes) - SHELF_FIELDS
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {changes['status']!r}")
    rating = changes.get("rating")
    if rating is not None and not (isinstance(rating, int) and 1 <= rating <= 5):
        raise ValueError("rating must be an integer from 1 to 5, or null")
    notes = _clean_text(changes.get("notes"))
    if notes is not None and len(notes) > 5000:
        raise ValueError("notes are limited to 5000 characters")

    with conn:
        if conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            return False

        # Make sure a shelf row exists, then update it
        conn.execute(
            "INSERT OR IGNORE INTO shelf(book_id, status) VALUES (?, 'want')", (book_id,)
        )

        status = changes.get("status")
        if status == "reading":
            conn.execute(
                """
                UPDATE shelf
                SET status = 'reading',
                    started_on = COALESCE(started_on, date('now', 'localtime')),
                    finished_on = NULL
                WHERE book_id = ?
                """,
                (book_id,),
            )
        elif status == "read":
            conn.execute(
                """
                UPDATE shelf
                SET status = 'read',
                    finished_on = COALESCE(finished_on, date('now', 'localtime'))
                WHERE book_id = ?
                """,
                (book_id,),
            )
        elif status == "want":
            conn.execute(
                """
                UPDATE shelf
                SET status = 'want', started_on = NULL, finished_on = NULL
                WHERE book_id = ?
                """,
                (book_id,),
            )

        if "rating" in changes:
            conn.execute("UPDATE shelf SET rating = ? WHERE book_id = ?", (rating, book_id))
        if "notes" in changes:
            conn.execute("UPDATE shelf SET notes = ? WHERE book_id = ?", (notes, book_id))

    return True


def delete_book(conn: sqlite3.Connection, book_id: int) -> bool:
    """Delete a book (its shelf row and author links cascade). Returns False if missing."""
    with conn:
        cursor = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        if cursor.rowcount == 0:
            return False
        _drop_orphan_authors(conn)  # authors that no longer have any book
    return True
