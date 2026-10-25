# Bookshelf 📚

**Bookshelf** is a fast, lightweight, self-hosted web application for cataloging your physical book library by scanning ISBN barcodes with your phone or device camera.

---

## ✨ Features

- **Instant Barcode Scanning:** Scan book barcodes directly using your device's camera via the web app.
- **Batch Import:** Upload multiple photos from your gallery at once with automatic client-side image resizing and concurrent background uploads.
- **Open Library Integration:** Automatically fetches title, authors, publisher, publication year, and cover art.
- **Shelf Management:** Group books by status (*Want to Read*, *Reading*, *Read*).
- **Ratings & Dates:** Track start/finish dates automatically and rate finished books (1–5 stars).
- **Responsive & Dark Mode:** Native system dark-mode support and mobile-first, app-like responsive UI.
- **Single-File Frontend:** Built with clean Vanilla JavaScript—no build steps or node_modules required.

---

## 🏗️ Architecture & Stack

- **Backend:** Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/)
- **Database:** SQLite (with foreign keys and JSON aggregation enabled)
- **Computer Vision:** OpenCV & PyZBar (ISBN-13 barcode detection & validation)
- **Metadata Provider:** [Open Library Books API](https://openlibrary.org/dev/docs/api/books)
- **Frontend:** HTML5, CSS3, Vanilla JavaScript ES6+

---

## 🛠️ Requirements & System Dependencies

Before running the application, ensure you have system-level support for barcode scanning libraries:

### Linux (Debian / Ubuntu)
```bash
sudo apt-get update
sudo apt-get install -y libzbar0 libgl1-mesa-glx
```

### macOS
```bash
brew install zbar
```

---

## 🚀 Quickstart

### 1. Clone the repository
```bash
git clone https://github.com/your-username/bookshelf.git
cd bookshelf
```

### 2. Set up Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Required Python packages:**
> - `fastapi`
> - `uvicorn[standard]`
> - `pyzbar`
> - `opencv-python-headless`
> - `requests`
> - `pydantic`

### 3. Initialize Database & Run Server
```bash
export BOOKSHELF_DB="data/library.db"  # Optional path override
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in your web browser.

---

## 🗺️ Development Roadmap & PR Plan

The project is structured into incremental, focused PR branches:

### Phase 1: Quick Wins (Front-End Only)
- **`feat/batch-import`**: Multi-photo import from gallery with client-side canvas downscaling (max 2000px edge), concurrency control, progress tracking, and retry support for failed uploads.
- **`feat/sort-filter`**: Client-side sorting (recently added, title, author, year, rating, finished date), filtering (rating, language, year), search indexing, and `localStorage` persistence.

### Phase 2: Schema & Core Enhancements
- **`chore/migrations`**: Migration runner leveraging `PRAGMA user_version` and numbered `.sql` files to manage schema changes (`description`, `work_key`, `source`, `owned`).
- **`feat/book-detail`**: Lazy-fetched work synopses from Open Library (`/works/OL...W.json`), local caching, and editable book details (notes, corrected titles/covers).
- **`feat/search-and-manual-add`**: Unified search input supporting ISBN lookup and text search (`/search?q=`) backed by Open Library's search API, complete with duplicate warnings and manual entry fallbacks.

### Phase 3: Integrations & Intelligence
- **`feat/kobo-import`**: One-way import of reading states, time spent reading, highlights, and annotations directly from Kobo e-reader SQLite databases (`.kobo/KoboReader.sqlite`), complete with an unmatched title resolution screen.
- **`feat/ai-taste`**: Optional Gemini Flash integration (secured server-side via `GEMINI_MODEL` and `.env`) to compute book taste scores, generate recommendations validated against Open Library search results, and cache outcomes in `ai_results`.

---

## 📡 API Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/books` | Retrieve books in library (Filterable by `?status=`) |
| `POST` | `/scan` | Upload photo, decode ISBN, fetch metadata, and save book |
| `PATCH` | `/books/{id}` | Update shelf status and/or rating |
| `DELETE` | `/books/{id}` | Remove a book from the library |
| `GET` | `/lookup/{isbn}` | Direct lookup against Open Library |
| `GET` | `/health` | Health check endpoint |

---

## 🧪 License

Distributed under the MIT License. See `LICENSE` for more information.