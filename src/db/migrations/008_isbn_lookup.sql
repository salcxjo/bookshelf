-- 008: the title-and-author lookup now also fills in missing ISBNs (not just covers), so books that
-- were already looked up for a cover and not found, but still have no ISBN, get one more try.
-- (cover_checked_on now means "looked up on Open Library by title and author".)
UPDATE books SET cover_checked_on = NULL WHERE isbn IS NULL;
