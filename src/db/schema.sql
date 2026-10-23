PRAGMA foreign_keys = ON;  -- must be set on every connection in SQLite

CREATE TABLE IF NOT EXISTS authors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS books (
    id         INTEGER PRIMARY KEY,
    isbn       TEXT UNIQUE,
    title      TEXT NOT NULL,
    subtitle   TEXT,
    language   TEXT NOT NULL DEFAULT 'en',
    publisher  TEXT,
    year       INTEGER,
    cover_path TEXT,
    added_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id   INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, author_id)
);

CREATE TABLE IF NOT EXISTS shelf (
    book_id     INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('want', 'reading', 'read')),
    rating      INTEGER CHECK (rating BETWEEN 1 AND 5),
    started_on  TEXT,   -- ISO date: YYYY-MM-DD
    finished_on TEXT
);

CREATE INDEX IF NOT EXISTS idx_shelf_status       ON shelf(status);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);