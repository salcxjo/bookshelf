import sqlite3
from pathlib import Path
'''

{'isbn': '9781234567897', 'title': 'Construction Cost Consultant for Residential Commercial and Industrial Construction Projects', 'subtitle': None, 'authors': ['Toqeer Khan'], 'publisher': 'COMET Estimating LLC', 'year': 2026, 'cover_url': 'https://covers.openlibrary.org/b/id/15234162-L.jpg'}

'''

class RepoManager:
    def __init__(self, db_name="library.db"):
        # Explicitly place it in your project root or a known absolute path for debugging
        project_root = Path(__file__).resolve().parent.parent.parent # adjust based on your structure
        db_path = project_root / db_name

        print(f"Connecting to database at: {db_path}")
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.cursor = self.conn.cursor()

    def add_book(self, book: dict) -> None:
        try:
            # 1. Insert the book
            self.cursor.execute("""
                INSERT OR IGNORE INTO books(isbn, title, language, publisher, year, cover_url) VALUES
                (:isbn, :title, :language, :publisher, :year, :cover_url)
            """, {
                "isbn": book.get("isbn"),
                "title": book.get("title"),
                "language": book.get("language", "en"),
                "publisher": book.get("publisher"),
                "year": book.get("year"),
                "cover_url": book.get("cover_url")
            })

            # Get the book's primary key
            self.cursor.execute("SELECT id FROM books WHERE isbn = ?", (book.get("isbn"),))
            row = self.cursor.fetchone()
            if not row:
                print("Failed to retrieve book ID after insert.")
                return
            book_id = row[0]

            # 2. Insert authors and link them
            authors = book.get("authors", [])
            for author_name in authors:
                self.cursor.execute("""
                    INSERT OR IGNORE INTO authors(name) VALUES (?)
                """, (author_name,))

                self.cursor.execute("SELECT id FROM authors WHERE name = ?", (author_name,))
                author_row = self.cursor.fetchone()
                if author_row:
                    author_id = author_row[0]
                    self.cursor.execute("""
                        INSERT OR IGNORE INTO book_authors(book_id, author_id) VALUES (?, ?)
                    """, (book_id, author_id))

            # 3. Explicitly commit changes
            self.conn.commit()
            print("Successfully added book to database!")

        except Exception as e:
            print(f"Database error during add_book: {e}")
            self.conn.rollback()
    def set_status(self, isbn: str, status: str, rating: int = None, started_on: str = None, finished_on: str = None):
        # Example implementation for updating/adding to the shelf
        self.cursor.execute("""
            INSERT INTO shelf(book_id, status, rating, started_on, finished_on)
            SELECT id, ?, ?, ?, ? FROM books WHERE isbn = ?
            ON CONFLICT(book_id) DO UPDATE SET
                status = excluded.status,
                rating = excluded.rating,
                started_on = excluded.started_on,
                finished_on = excluded.finished_on
        """, (status, rating, started_on, finished_on, isbn))
        self.conn.commit()

    def list_by_status(self, status: str) -> list:
        self.cursor.execute("""
            SELECT b.isbn, b.title, b.publisher, b.year, b.cover_url, s.status, s.rating
            FROM books b
            JOIN shelf s ON b.id = s.book_id
            WHERE s.status = ?
        """, (status,))

        # Convert rows to dictionaries for easy reading
        columns = [col[0] for col in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]