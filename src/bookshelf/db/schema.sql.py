CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    isbn TEXT UNIQUE,
    title TEXT NOT NULL,
    cover_path TEXT
);
-- TODO: authors, book_authors, shelf(status CHECK IN ('reading','read','want'))