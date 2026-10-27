-- 006: Goodreads import support. The Goodreads "Book Id" a book was imported from / matched to,
-- so re-importing a newer export finds it directly instead of guessing by title again.
ALTER TABLE books ADD COLUMN goodreads_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_goodreads_id ON books(goodreads_id) WHERE goodreads_id IS NOT NULL;
