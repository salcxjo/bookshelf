"""Cover image URLs that need no network lookup."""


def for_isbn(isbn: str | None) -> str | None:
    """Open Library serves covers by ISBN. ?default=false makes a missing cover a 404,
    which the page treats as 'no cover' (it shows the title instead of a blank image)."""
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false" if isbn else None
