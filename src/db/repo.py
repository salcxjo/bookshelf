"""All SQL lives here. Every function takes an open connection as its first argument."""
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


def get_book_by_isbn(conn: sqlite3.Connection, isbn: str) -> dict | None:
    """Return a book in the same shape as fetch_by_isbn, plus its id and shelf status.

    `status` is None if the book has no shelf row yet.
    """
    row = conn.execute(
        """
        SELECT b.id, b.isbn, b.title, b.subtitle, b.publisher, b.year,
               b.cover_url, s.status
        FROM books b
        LEFT JOIN shelf s ON s.book_id = b.id
        WHERE b.isbn = ?
        """,
        (isbn,),
    ).fetchone()
    if row is None:
        return None

    book = dict(row)
    book["authors"] = [
        r["name"]
        for r in conn.execute(
            """
            SELECT a.name
            FROM book_authors ba
            JOIN authors a ON a.id = ba.author_id
            WHERE ba.book_id = ?
            ORDER BY ba.rowid  -- insertion order, i.e. the order the API listed them
            """,
            (book["id"],),
        )
    ]
    return book


def set_status(conn: sqlite3.Connection, book_id: int, status: str) -> None:
    """Create or update the shelf row for a book.

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