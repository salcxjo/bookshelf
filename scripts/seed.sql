INSERT INTO authors (id, name) VALUES
    (1, 'Salman Rushdie'),
    (2, 'Benjamín Labatut'),
    (3, 'Louis de Bernières'),
    (4, 'Jalal Al-e Ahmad'),
    (5, 'Paulo Coelho');

INSERT INTO books (id, isbn, title, language, year) VALUES
    (1, NULL, 'The Satanic Verses',                        'en', 1988),
    (2, NULL, 'When We Cease to Understand the World',     'en', 2020),
    (3, NULL, 'Birds Without Wings',                       'en', 2004),
    (4, NULL, 'Modir-e Madrese',                           'fa', 1958),
    (5, NULL, 'Eleven Minutes',                            'en', 2003);

INSERT INTO book_authors (book_id, author_id) VALUES
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5);

INSERT INTO shelf (book_id, status, rating, started_on, finished_on) VALUES
    (1, 'read',    4,    '2025-03-01', '2025-04-10'),
    (2, 'reading', NULL, '2026-10-01', NULL),
    (3, 'read',    5,    NULL,         NULL),
    (4, 'want',    NULL, NULL,         NULL),
    (5, 'read',    3,    NULL,         NULL);