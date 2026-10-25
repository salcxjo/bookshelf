import os
import sqlite3
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, status

from db import repo
from lookup.openlibrary import fetch_by_isbn
from vision.barcode import decode_isbn_from_bytes

# src/bookshelf/main.py -> parents[2] is the repo root. Override with BOOKSHELF_DB.
DB_PATH = Path(
    os.environ.get("BOOKSHELF_DB", Path(__file__).resolve().parents[2] / "data" / "library.db")
)
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # phone photos are usually 2-10 MB

app = FastAPI()


def get_db():
    """One connection per request; tests replace this with app.dependency_overrides."""
    conn = repo.get_connection(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


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


@app.post("/scan")
def scan(
    file: UploadFile,
    response: Response,
    shelf_status: str = "want",
    conn: sqlite3.Connection = Depends(get_db),
):
    """Photo of a barcode in -> book saved to the library and returned.

    200 = already in the library, 201 = newly added.
    """
    if shelf_status not in repo.STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"shelf_status must be one of {repo.STATUSES}",
        )

    data = file.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="image too large",
        )

    # 1. Photo -> ISBN
    try:
        isbn = decode_isbn_from_bytes(data)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not an image")
    if isbn is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no barcode found"
        )

    # 2. Already in the library? Skip the API call.
    existing = repo.get_book_by_isbn(conn, isbn)
    if existing is not None:
        return existing

    # 3. ISBN -> metadata
    try:
        book = fetch_by_isbn(isbn)
    except requests.RequestException:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="External API error while fetching book details",
        )
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book with ISBN {isbn} not found",
        )

    # 4. Save and return the stored version
    try:
        book_id = repo.add_book(conn, book)
    except repo.DuplicateBookError:  # two scans of the same book racing each other
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already in library")
    repo.set_status(conn, book_id, shelf_status)

    response.status_code = status.HTTP_201_CREATED
    return repo.get_book_by_isbn(conn, isbn)