import requests
from fastapi import FastAPI, HTTPException
from db.repo import RepoManager
from lookup.openlibrary import fetch_by_isbn
db = RepoManager()
app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/lookup/{isbn}")
def lookup(isbn: str):
    try:
        book = fetch_by_isbn(isbn)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Open Library lookup failed")
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book
if __name__ == "__main__":
    book_data = {
        'isbn': '9781234567897',
        'title': 'Construction Cost Consultant for Residential Commercial and Industrial Construction Projects',
        'subtitle': None,
        'authors': ['Toqeer Khan'],
        'publisher': 'COMET Estimating LLC',
        'year': 2026,
        'cover_url': 'https://covers.openlibrary.org/b/id/15234162-L.jpg'
    }
    db.add_book(book_data)
    # Add this temporarily in your __main__ block to test reading it back
    db.cursor.execute("SELECT * FROM books;")
    print("Rows in books table:", db.cursor.fetchall())

    db.cursor.execute("SELECT * FROM authors;")
    print("Rows in authors table:", db.cursor.fetchall())