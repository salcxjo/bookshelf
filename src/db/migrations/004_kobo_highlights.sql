-- 004: Kobo import support.

-- The Kobo ContentID a book was imported from / matched to, so re-imports find it directly
-- instead of guessing by title again.
ALTER TABLE books ADD COLUMN kobo_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_kobo_id ON books(kobo_id) WHERE kobo_id IS NOT NULL;

-- Reading progress reported by the e-reader.
ALTER TABLE shelf ADD COLUMN percent_read INTEGER CHECK (percent_read BETWEEN 0 AND 100);
ALTER TABLE shelf ADD COLUMN seconds_read INTEGER CHECK (seconds_read >= 0);
ALTER TABLE shelf ADD COLUMN last_read_on TEXT;   -- ISO date

-- Highlights and annotations.
CREATE TABLE IF NOT EXISTS highlights (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id          INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kobo_bookmark_id TEXT UNIQUE,      -- NULL for highlights that didn't come from a Kobo
    text             TEXT,
    note             TEXT,
    chapter          TEXT,
    created_on       TEXT,             -- ISO date
    CHECK (text IS NOT NULL OR note IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_highlights_book ON highlights(book_id);
