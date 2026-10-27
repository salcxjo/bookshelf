-- 007: covers for books that have an ISBN but no cover yet (for example books imported from
-- Kobo or Goodreads before covers were set). Open Library serves covers by ISBN, so this needs
-- no network access; a cover that doesn't exist just shows the title, like any other missing one.
UPDATE books
SET cover_url = 'https://covers.openlibrary.org/b/isbn/' || isbn || '-M.jpg?default=false'
WHERE cover_url IS NULL AND isbn IS NOT NULL;

-- Set once a book has been looked up by title and author without finding a cover, so
-- "Find missing covers" doesn't ask Open Library about the same book again and again.
ALTER TABLE books ADD COLUMN cover_checked_on TEXT;
