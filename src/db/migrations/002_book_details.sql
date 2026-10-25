-- 002: columns needed by synopsis, manual add, owned-vs-wishlist and later imports.

-- Synopsis text. NULL = never fetched yet; '' = fetched, Open Library has none.
ALTER TABLE books ADD COLUMN description TEXT;

-- Open Library work id such as 'OL45883W'. One work has many editions (ISBNs), so this
-- is what lets us warn "you already have this book" across editions.
ALTER TABLE books ADD COLUMN work_key TEXT;

-- How the book got into the library.
ALTER TABLE books ADD COLUMN source TEXT NOT NULL DEFAULT 'scan'
    CHECK (source IN ('scan', 'search', 'manual', 'kobo', 'import'));

-- 1 = on my shelf, 0 = wishlist only.
ALTER TABLE books ADD COLUMN owned INTEGER NOT NULL DEFAULT 1 CHECK (owned IN (0, 1));

-- Rows with no ISBN can only have come from hand-written SQL (e.g. seed.sql).
UPDATE books SET source = 'manual' WHERE isbn IS NULL;

CREATE INDEX IF NOT EXISTS idx_books_work_key ON books(work_key);
