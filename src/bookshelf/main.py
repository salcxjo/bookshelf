import os
import sqlite3
from pathlib import Path
from typing import Literal

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

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


class ShelfUpdate(BaseModel):
    """Body of PATCH /books/{id}. Send only the fields you want to change."""

    status: Literal["want", "reading", "read"] | None = None
    rating: int | None = Field(default=None, ge=1, le=5)  # null clears the rating


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


@app.get("/books")
def list_books(
    shelf_status: str | None = Query(default=None, alias="status"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """The whole library, newest first. Optional filter: /books?status=read"""
    try:
        return repo.list_books(conn, shelf_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.patch("/books/{book_id}")
def update_book(book_id: int, update: ShelfUpdate, conn: sqlite3.Connection = Depends(get_db)):
    """Change a book's status and/or rating."""
    changes = update.model_dump(exclude_unset=True)  # only what the client actually sent
    if not changes:
        raise HTTPException(
            status_code=422, detail="nothing to update"
        )
    try:
        found = repo.update_shelf(conn, book_id, changes)
    except ValueError as exc:  # e.g. "status": null
        raise HTTPException(status_code=422, detail=str(exc))
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return repo.get_book_by_id(conn, book_id)


@app.delete("/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(book_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not repo.delete_book(conn, book_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
            status_code=422,
            detail=f"shelf_status must be one of {repo.STATUSES}",
        )

    data = file.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="image too large",
        )

    # 1. Photo -> ISBN
    try:
        isbn = decode_isbn_from_bytes(data)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not an image")
    if isbn is None:
        raise HTTPException(
            status_code=422, detail="no barcode found"
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
from fastapi.staticfiles import StaticFiles   # add with the other imports

WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")   # must be last