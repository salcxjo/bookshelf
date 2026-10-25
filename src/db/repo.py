"""All SQL lives here. Every function takes an open connection as its first argument."""
import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
STATUSES = ("want", "reading", "read")


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
    """Create all tables/indexes from schema.sql (safe to run repeatedly)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

# One query shape for every read, so get/list always return the same dict:
# the book's columns, its shelf columns (None if unshelved) and `authors` as a
# list in the order they were inserted (ba.rowid).
_BOOK_SELECT = """
    SELECT b.id, b.isbn, b.title, b.subtitle, b.publisher, b.year, b.cover_url,
           s.status, s.rating, s.started_on, s.finished_on,
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


def _to_book(row: sqlite3.Row) -> dict:
    book = dict(row)
    book["authors"] = json.loads(book["authors"])
    return book


def get_book_by_isbn(conn: sqlite3.Connection, isbn: str) -> dict | None:
    row = conn.execute(_BOOK_SELECT + " WHERE b.isbn = ?", (isbn,)).fetchone()
    return _to_book(row) if row else None


def get_book_by_id(conn: sqlite3.Connection, book_id: int) -> dict | None:
    row = conn.execute(_BOOK_SELECT + " WHERE b.id = ?", (book_id,)).fetchone()
    return _to_book(row) if row else None


def list_books(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """All books, newest first, optionally only those with one shelf status."""
    sql = _BOOK_SELECT
    params: tuple = ()
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        sql += " WHERE s.status = ?"
        params = (status,)
    sql += " ORDER BY b.added_at DESC, b.id DESC"
    return [_to_book(row) for row in conn.execute(sql, params)]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def add_book(conn: sqlite3.Connection, book: dict) -> int:
    """Insert a book, its authors, and the links between them in one transaction.

    Returns the new book's id. Raises DuplicateBookError if the ISBN exists.
    """
    # `with conn:` commits on success and rolls everything back on any exception
    with conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO books(isbn, title, subtitle, publisher, year, cover_url)
                VALUES (:isbn, :title, :subtitle, :publisher, :year, :cover_url)
                """,
                {
                    "isbn": book.get("isbn"),
                    "title": book.get("title"),
                    "subtitle": book.get("subtitle"),
                    "publisher": book.get("publisher"),
                    "year": book.get("year"),
                    "cover_url": book.get("cover_url"),
                },
            )
        except sqlite3.IntegrityError as exc:
            # Only translate the duplicate-ISBN case; other violations (e.g. a
            # missing title) should surface as the real error.
            if "books.isbn" in str(exc):
                raise DuplicateBookError(book.get("isbn")) from exc
            raise
        book_id = cursor.lastrowid

        for name in book.get("authors", []):
            conn.execute("INSERT OR IGNORE INTO authors(name) VALUES (?)", (name,))
            author_id = conn.execute(
                "SELECT id FROM authors WHERE name = ?", (name,)
            ).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO book_authors(book_id, author_id) VALUES (?, ?)",
                (book_id, author_id),
            )

    return book_id


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

    `changes` may contain "status" and/or "rating" (None clears the rating).
    Status changes keep the dates in step, using the server's local date:
      -> reading: started_on is set if empty, finished_on is cleared
      -> read:    finished_on is set if empty (started_on is left alone)
      -> want:    both dates are cleared
    Raises ValueError for unknown fields or invalid values.
    """
    unknown = set(changes) - {"status", "rating"}
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {changes['status']!r}")
    rating = changes.get("rating")
    if rating is not None and not (isinstance(rating, int) and 1 <= rating <= 5):
        raise ValueError("rating must be an integer from 1 to 5, or null")

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

    return True


def delete_book(conn: sqlite3.Connection, book_id: int) -> bool:
    """Delete a book (its shelf row and author links cascade). Returns False if missing."""
    with conn:
        cursor = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        if cursor.rowcount == 0:
            return False
        # Drop authors that no longer have any book
        conn.execute("DELETE FROM authors WHERE id NOT IN (SELECT author_id FROM book_authors)")
    return True