import sqlite3
import tempfile
from pathlib import Path

import pytest

import db


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def test_page_rule_rejects_zero(db_path):
    book = {"title": "Test", "author": "A", "page_count": 100, "status": "Okunuyor"}
    import application
    created, book_id = db.add_book(book, db_path)
    assert created
    assert application.validate_page_read({"page_count": 100, "current_page": 20}, 0, 20) is not None


def test_page_rule_rejects_decrease_and_overflow(db_path):
    import application
    book = {"page_count": 100, "current_page": 80}
    assert application.validate_page_read(book, 70, 80)
    assert application.validate_page_read(book, 101, 80)
    assert application.validate_page_read(book, 90, 80) is None


def test_duplicate_book(db_path):
    book = {"title": "Suç ve Ceza", "author": "Dostoyevski", "isbn": "123", "page_count": 700}
    created, first_id = db.add_book(book, db_path)
    created_again, same_id = db.add_book(book, db_path)
    assert created
    assert not created_again
    assert first_id == same_id


def test_annual_goal_status(monkeypatch, db_path):
    import application
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setitem(application.app.config, "TZ", "Europe/Istanbul")
    conn = db.get_db(db_path)
    conn.execute("UPDATE settings SET value = '24' WHERE key = 'yearly_goal'")
    conn.commit()
    conn.close()
    count, goal, status = application.yearly_goal_status()
    assert count == 0
    assert goal == 24
    assert "24 kitap gerisindesin" in status


def test_finish_estimate(monkeypatch, db_path):
    import application
    from datetime import datetime, timedelta
    monkeypatch.setattr(db, "DB_PATH", db_path)
    created, book_id = db.add_book({
        "title": "Tahmin Test",
        "page_count": 140,
        "status": "Okunuyor",
    }, db_path)
    conn = db.get_db(db_path)
    today = datetime.now(application.ISTANBUL).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO reading_logs(book_id, page, log_date) VALUES (?, 70, ?)", (book_id, today))
    conn.execute("UPDATE books SET current_page = 70 WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()
    assert application.estimate_days(book_id) == 7


def test_login_and_library(client):
    response = client.post("/login", data={"username": "test", "password": "123"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Okunuyor".encode() in response.data
