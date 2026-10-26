-- 005: saved AI answers, so each book is only sent to the model once per set of inputs.
CREATE TABLE IF NOT EXISTS ai_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('match', 'similar')),
    model       TEXT NOT NULL,
    input_hash  TEXT NOT NULL,       -- hash of everything the answer depended on; a change means "ask again"
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (book_id, kind)
);
