import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Response, UploadFile, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bookshelf import llm, taste
from db import repo
from db.migrate import migrate
from bookshelf.importers.kobo import KoboFormatError, apply_plan, plan_import, read_kobo
from bookshelf.isbn import normalize_isbn
from lookup.openlibrary import fetch_by_isbn, fetch_description, search_works
from vision.barcode import decode_isbn_from_bytes

load_dotenv()  # reads .env (GEMINI_API_KEY and friends) if there is one; real environment variables win

# src/bookshelf/main.py -> parents[2] is the repo root. Override with BOOKSHELF_DB.
DB_PATH = Path(
    os.environ.get("BOOKSHELF_DB", Path(__file__).resolve().parents[2] / "data" / "library.db")
)
WEB_DIR = Path(__file__).resolve().parents[2] / "web"   # the page served at /
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # phone photos are usually 2-10 MB
MAX_KOBO_BYTES = 500 * 1024 * 1024  # KoboReader.sqlite is usually a few MB to a few tens of MB



@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: create data/ if needed and bring the database schema up to date."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = repo.get_connection(DB_PATH)
    try:
        migrate(conn)
    finally:
        conn.close()
    yield


app = FastAPI(lifespan=lifespan)


def get_db():
    """One connection per request; tests replace this with app.dependency_overrides."""
    conn = repo.get_connection(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


class BookUpdate(BaseModel):
    """Body of PATCH /books/{id}. Send only the fields you want to change."""

    # your relationship to the book (shelf)
    status: Literal["want", "reading", "read"] | None = None
    rating: int | None = Field(default=None, ge=1, le=5)  # null clears the rating
    notes: str | None = Field(default=None, max_length=5000)  # blank clears the notes
    # the book's own details
    title: str | None = Field(default=None, max_length=300)
    subtitle: str | None = Field(default=None, max_length=300)
    authors: list[str] | None = Field(default=None, max_length=20)
    publisher: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=0, le=2100)
    cover_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://")
    owned: bool | None = None


class NewBook(BaseModel):
    """Body of POST /books: a search result, or a book typed in by hand."""

    title: str = Field(min_length=1, max_length=300)
    authors: list[str] = Field(default_factory=list, max_length=20)
    subtitle: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=0, le=2100)
    isbn: str | None = Field(default=None, max_length=32)
    cover_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://")
    work_key: str | None = Field(default=None, pattern=r"^OL\d+W$")
    source: Literal["search", "manual"] = "manual"
    status: Literal["want", "reading", "read"] = "want"
    owned: bool = True
    force: bool = False  # add even though a book with the same title/work already exists


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


@app.get("/search")
def search(
    q: str | None = None,
    title: str | None = None,
    author: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Find books to add. `q` may be an ISBN (10 or 13 digits, hyphens allowed) or free text
    such as a title and/or an author; `title` and `author` search those fields specifically.
    Each result says whether the library already has it (`in_library` = book id or null)."""
    q = (q or "").strip()
    if not (q or (title or "").strip() or (author or "").strip()):
        raise HTTPException(status_code=422, detail="type a title, an author or an ISBN")

    isbn = normalize_isbn(q) if q else None
    try:
        if isbn:
            found = fetch_by_isbn(isbn)
            results = [{**found, "work_key": None, "edition_count": None}] if found else []
        else:
            results = search_works(q=q or None, title=(title or "").strip() or None,
                                   author=(author or "").strip() or None, limit=12)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Open Library lookup failed")

    index = repo.library_index(conn)
    for result in results:
        hit = repo.match_in_index(index, isbn=result.get("isbn"), work_key=result.get("work_key"),
                                  title=result["title"], authors=result["authors"])
        result["in_library"] = hit[0] if hit else None
    return {"kind": "isbn" if isbn else "text", "isbn": isbn, "results": results}


@app.post("/books", status_code=status.HTTP_201_CREATED)
def add_book(new: NewBook, conn: sqlite3.Connection = Depends(get_db)):
    """Add a book from a search result or by hand. 409 means it looks like a duplicate;
    `detail.can_force` says whether sending `force: true` can override that."""
    isbn = None
    if new.isbn and new.isbn.strip():
        isbn = normalize_isbn(new.isbn)
        if isbn is None:
            raise HTTPException(status_code=422, detail="That isn’t a valid ISBN")

    hit = repo.find_duplicate(conn, isbn=isbn, work_key=new.work_key, title=new.title, authors=new.authors)
    if hit is not None and (hit[1] == "isbn" or not new.force):
        existing, reason = hit
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "message": f"Already in the library: {existing['title']}",
            "reason": reason, "book": existing, "can_force": reason != "isbn",
        })

    data = new.model_dump(exclude={"force", "status"})
    data["isbn"] = isbn
    if new.source == "search" and isbn:
        # Search results only know a little; fill the gaps from the edition record (best effort)
        try:
            edition = fetch_by_isbn(isbn) or {}
        except requests.RequestException:
            edition = {}
        for key in ("subtitle", "publisher", "year", "cover_url"):
            blank = data.get(key) is None or (isinstance(data[key], str) and not data[key].strip())
            if blank and edition.get(key):
                data[key] = edition[key]

    try:
        book_id = repo.add_book(conn, data)
    except ValueError as exc:  # e.g. a title that is only spaces
        raise HTTPException(status_code=422, detail=str(exc))
    except repo.DuplicateBookError:  # lost a race with another request
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "message": "Already in the library", "reason": "isbn", "book": repo.get_book_by_isbn(conn, isbn), "can_force": False})
    repo.set_status(conn, book_id, new.status)
    return repo.get_book_by_id(conn, book_id)


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


@app.get("/books/{book_id}")
def get_book(book_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """One book, including its synopsis if one has been fetched (see /synopsis)."""
    book = repo.get_book_by_id(conn, book_id, include_description=True)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return book


@app.get("/books/{book_id}/synopsis")
def synopsis(book_id: int, refresh: bool = False, conn: sqlite3.Connection = Depends(get_db)):
    """The book's synopsis, fetched from Open Library the first time and cached afterwards.

    An empty description means Open Library has none. ?refresh=true looks again.
    """
    book = repo.get_book_by_id(conn, book_id, include_description=True)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    if book["description"] is not None and not refresh:
        return {"description": book["description"], "work_key": book["work_key"], "cached": True}

    try:
        work_key, description = fetch_description(
            isbn=book["isbn"], work_key=book["work_key"], title=book["title"], authors=book["authors"]
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Open Library lookup failed")

    if description or book["description"] is None:  # a refresh that finds nothing keeps the old text
        repo.set_description(conn, book_id, description, work_key)
    else:
        description = book["description"]
    return {"description": description, "work_key": work_key or book["work_key"], "cached": False}


@app.patch("/books/{book_id}")
def update_book(book_id: int, update: BookUpdate, conn: sqlite3.Connection = Depends(get_db)):
    """Change a book's details and/or your shelf data for it."""
    changes = update.model_dump(exclude_unset=True)  # only what the client actually sent
    if not changes:
        raise HTTPException(status_code=422, detail="nothing to update")
    for key in ("status", "title", "authors", "owned"):  # these can't be cleared
        if key in changes and changes[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")

    book_changes = {k: v for k, v in changes.items() if k in repo.BOOK_FIELDS}
    shelf_changes = {k: v for k, v in changes.items() if k in repo.SHELF_FIELDS}
    try:
        found = repo.update_book(conn, book_id, book_changes) if book_changes else True
        if found and shelf_changes:
            found = repo.update_shelf(conn, book_id, shelf_changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return repo.get_book_by_id(conn, book_id)


@app.get("/books/{book_id}/highlights")
def book_highlights(book_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if repo.get_book_by_id(conn, book_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return repo.list_highlights(conn, book_id)


# --------------------------------------------------------------------------- #
# AI (optional; needs GEMINI_API_KEY)
# --------------------------------------------------------------------------- #

def _ai_book(conn: sqlite3.Connection, book_id: int) -> dict:
    book = repo.get_book_by_id(conn, book_id, include_description=True)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    if not llm.is_configured():
        raise HTTPException(status_code=503, detail="AI features are off. Put GEMINI_API_KEY in .env and restart the server.")
    return book


def _ask_model(function, *args):
    """Run a taste/LLM function, turning its failures into sensible HTTP errors."""
    try:
        return function(*args)
    except taste.NotEnoughRatings as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except llm.LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except llm.LLMRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/ai/status")
def ai_status():
    """Lets the page decide whether to show the AI buttons."""
    on = llm.is_configured()
    return {"enabled": on, "model": llm.model_name() if on else None, "min_rated": taste.MIN_RATED}


@app.post("/books/{book_id}/ai/match")
def ai_match(book_id: int, refresh: bool = False, conn: sqlite3.Connection = Depends(get_db)):
    """How well does this book fit the taste shown by your ratings? Saved until your ratings
    or the book's details change; ?refresh=true asks again."""
    book = _ai_book(conn, book_id)
    profile = _ask_model(taste.taste_profile, conn, book_id)
    key = taste.match_key(profile, book)
    saved = repo.get_ai_result(conn, book_id, "match")
    if saved and saved["input_hash"] == key and not refresh:
        return {**saved["result"], "cached": True, "created_at": saved["created_at"]}
    result = _ask_model(taste.score_book, book, profile)
    repo.save_ai_result(conn, book_id, "match", llm.model_name(), key, result)
    return {**result, "cached": False, "created_at": None}


@app.post("/books/{book_id}/ai/similar")
def ai_similar(book_id: int, refresh: bool = False, conn: sqlite3.Connection = Depends(get_db)):
    """Up to five books like this one. Every suggestion has been checked against Open Library,
    so none of them is an invented title."""
    book = _ai_book(conn, book_id)
    key = taste.similar_key(book)
    saved = repo.get_ai_result(conn, book_id, "similar")
    cached = bool(saved and saved["input_hash"] == key and not refresh)
    if cached:
        data = saved["result"]
    else:
        data = _ask_model(taste.similar_books, book)
        repo.save_ai_result(conn, book_id, "similar", llm.model_name(), key, data)
    return {"books": taste.mark_in_library(conn, data["books"]), "dropped": data["dropped"], "cached": cached}


@app.get("/stats")
def stats(conn: sqlite3.Connection = Depends(get_db)):
    return repo.get_stats(conn)


@app.post("/import/kobo")
def import_kobo(
    file: UploadFile,
    apply: bool = False,
    include_unread: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Upload a KoboReader.sqlite. By default this only previews what would change;
    send ?apply=true to import. Books you already have are only ever moved forward."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "KoboReader.sqlite")
        size = 0
        with open(path, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_KOBO_BYTES:
                    raise HTTPException(status_code=413, detail="that file is too large to be a KoboReader.sqlite")
                out.write(chunk)
        try:
            data = read_kobo(path, include_unread=include_unread)
        except KoboFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    plan = plan_import(conn, data)
    report = apply_plan(conn, plan) if apply else plan["report"]
    report["items"] = report["items"][:1000]  # plenty for a preview
    return report


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


# Keep this LAST: a mount at "/" matches every path that no route above handled.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
