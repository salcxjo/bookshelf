import requests
from fastapi import FastAPI, HTTPException

from lookup.openlibrary import fetch_by_isbn

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