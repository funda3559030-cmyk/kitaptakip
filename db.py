import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


DB_PATH = Path("data") / "kitap.db"
TODAY = lambda: datetime.now().strftime("%Y-%m-%d")


def get_db(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    conn = get_db(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            cover_path TEXT,
            source_url TEXT,
            page_count INTEGER,
            isbn TEXT,
            publish_year TEXT,
            status TEXT NOT NULL DEFAULT 'Okunacak'
                CHECK(status IN ('Okunacak', 'Okunuyor', 'Okundu')),
            current_page INTEGER NOT NULL DEFAULT 0,
            rating INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
            notes TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS reading_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            page INTEGER NOT NULL CHECK(page >= 0),
            log_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR IGNORE INTO settings(key, value) VALUES ('yearly_goal', '24');
        """
    )
    conn.commit()
    conn.close()


def add_book(book: dict[str, Any], db_path: Optional[Path | str] = None) -> tuple[bool, int | None]:
    conn = get_db(db_path)
    isbn = (book.get("isbn") or "").strip()
    if isbn:
        existing = conn.execute("SELECT id FROM books WHERE isbn = ?", (isbn,)).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM books WHERE lower(title) = lower(?) AND lower(author) = lower(?)",
            (book["title"].strip(), (book.get("author") or "").strip()),
        ).fetchone()
    if existing:
        conn.close()
        return False, existing["id"]

    cursor = conn.execute(
        """
        INSERT INTO books(title, author, publisher, cover_path, source_url, page_count,
                          isbn, publish_year, status, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book["title"].strip(),
            (book.get("author") or "").strip(),
            (book.get("publisher") or "").strip(),
            book.get("cover_path"),
            book.get("source_url"),
            book.get("page_count"),
            isbn or None,
            book.get("publish_year"),
            book.get("status", "Okunacak"),
            TODAY() if book.get("status") == "Okunuyor" else None,
            TODAY() if book.get("status") == "Okundu" else None,
        ),
    )
    conn.commit()
    book_id = cursor.lastrowid
    conn.close()
    return True, book_id


def get_books(status: Optional[str] = None, db_path: Optional[Path | str] = None) -> list[sqlite3.Row]:
    conn = get_db(db_path)
    if status:
        rows = conn.execute("SELECT * FROM books WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows


def get_book(book_id: int, db_path: Optional[Path | str] = None) -> Optional[sqlite3.Row]:
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    return row


def update_status(book_id: int, status: str, db_path: Optional[Path | str] = None) -> tuple[bool, str]:
    if status not in {"Okunacak", "Okunuyor", "Okundu"}:
        return False, "Geçersiz raf."
    conn = get_db(db_path)
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        conn.close()
        return False, "Kitap bulunamadı."

    started_at = book["started_at"]
    finished_at = book["finished_at"]
    if status == "Okunuyor" and not started_at:
        started_at = TODAY()
    if status == "Okundu":
        page_count = book["page_count"] or 0
        if page_count > 0:
            conn.execute("UPDATE books SET current_page = ? WHERE id = ?", (page_count, book_id))
        finished_at = TODAY()
    elif status != "Okundu":
        finished_at = None

    conn.execute(
        "UPDATE books SET status = ?, started_at = ?, finished_at = ? WHERE id = ?",
        (status, started_at, finished_at, book_id),
    )
    conn.commit()
    conn.close()
    return True, "Raf güncellendi."
