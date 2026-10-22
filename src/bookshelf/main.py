from fastapi import FastAPI
import lookup.openlibrary
from src.bookshelf.lookup import openlibrary
from src.bookshelf.lookup.openlibrary import fetch_by_isbn

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}
def main():
    fetch_by_isbn("9780140328721")

if __name__ == "__main__":
    main()