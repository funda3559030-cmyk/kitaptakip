import os
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

import db
import scraper


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "yerel-gelistirme-anahtari")
app.config["TZ"] = "Europe/Istanbul"
ISTANBUL = ZoneInfo("Europe/Istanbul")

db.init_db()
Path("static/kapaklar").mkdir(parents=True, exist_ok=True)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def today() -> str:
    return datetime.now(ISTANBUL).strftime("%Y-%m-%d")


def validate_page_read(book, page: int, previous_page: int = 0) -> str | None:
    if page <= 0:
        return "Okunan sayfa 0'dan büyük olmalıdır."
    if page < previous_page:
        return "Okunan sayfa önceki kayıttan küçük olamaz."
    if book["page_count"] and page > book["page_count"]:
        return "Okunan sayfa toplam sayfa sayısını aşamaz."
    return None


def estimate_days(book_id: int) -> int | None:
    conn = db.get_db()
    start = (datetime.now(ISTANBUL).date() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT page, log_date FROM reading_logs WHERE book_id = ? AND log_date >= ? ORDER BY log_date, id",
        (book_id, start),
    ).fetchall()
    book = conn.execute("SELECT current_page, page_count FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    if not rows or not book or not book["page_count"]:
        return None
    remaining = book["page_count"] - book["current_page"]
    if remaining <= 0:
        return 0
    pages = rows[-1]["page"] - rows[0]["page"]
    if pages <= 0:
        pages = rows[-1]["page"]
    rate = pages / 7
    return max(1, round(remaining / rate)) if rate > 0 else None


def yearly_goal_status() -> tuple[int, int, str]:
    conn = db.get_db()
    goal_row = conn.execute("SELECT value FROM settings WHERE key = 'yearly_goal'").fetchone()
    goal = int(goal_row["value"]) if goal_row else 24
    year = datetime.now(ISTANBUL).year
    count = conn.execute(
        "SELECT COUNT(*) AS count FROM books WHERE status = 'Okundu' AND substr(finished_at,1,4) = ?",
        (str(year),),
    ).fetchone()["count"]
    conn.close()
    difference = goal - count
    if difference > 0:
        status = f"Hedefin {difference} kitap gerisindesin"
    elif difference < 0:
        status = f"Hedefini {abs(difference)} kitap geçtin"
    else:
        status = "Yıllık hedefini tamamladın"
    return count, goal, status


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "test" and request.form.get("password") == "123":
            session["logged_in"] = True
            return redirect(url_for("library"))
        flash("Kullanıcı adı veya şifre hatalı.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def library():
    active = request.args.get("raf", "Okunuyor")
    if active not in {"Okunacak", "Okunuyor", "Okundu"}:
        active = "Okunuyor"
    books = db.get_books(active)
    estimates = {book["id"]: estimate_days(book["id"]) for book in books if book["status"] == "Okunuyor"}
    conn = db.get_db()
    counts = {status: conn.execute("SELECT COUNT(*) AS count FROM books WHERE status = ?", (status,)).fetchone()["count"] for status in ["Okunacak", "Okunuyor", "Okundu"]}
    logs = {}
    for book in books:
        logs[book["id"]] = conn.execute(
            "SELECT * FROM reading_logs WHERE book_id = ? ORDER BY log_date DESC, id DESC LIMIT 1",
            (book["id"],),
        ).fetchone()
    conn.close()
    return render_template("library.html", books=books, active=active, estimates=estimates, counts=counts, logs=logs)


@app.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = []
    message = None
    if query:
        results, message = scraper.search_books(query)
    return render_template("search.html", query=query, results=results, message=message)


@app.route("/add", methods=["POST"])
@login_required
def add():
    title = request.form.get("title", "").strip()
    if not title:
        flash("Kitap adı gerekli.")
        return redirect(url_for("search"))
    source_url = request.form.get("source_url") or None
    details = scraper.get_book_details(source_url) if source_url else {}
    page_count = details.get("page_count") or request.form.get("page_count")
    try:
        page_count = int(page_count) if page_count else None
    except (TypeError, ValueError):
        page_count = None
    cover_path = scraper.download_cover(request.form.get("cover_url") or None, title)
    book = {
        "title": title,
        "author": request.form.get("author"),
        "publisher": request.form.get("publisher"),
        "cover_path": cover_path,
        "source_url": source_url,
        "page_count": page_count,
        "isbn": details.get("isbn"),
        "publish_year": details.get("publish_year"),
        "status": request.form.get("status", "Okunacak"),
    }
    created, book_id = db.add_book(book)
    flash("Kitap kitaphnenize eklendi." if created else "Bu kitap zaten kitaplığında.")
    return redirect(url_for("library", raf=book["status"]))


@app.route("/manual", methods=["POST"])
@login_required
def manual_add():
    title = request.form.get("title", "").strip()
    try:
        page_count = int(request.form.get("page_count") or 0)
    except ValueError:
        page_count = 0
    if not title or page_count <= 0:
        flash("Kitap adı ve 0'dan büyük sayfa sayısı gereklidir.")
        return redirect(url_for("search"))
    cover_path = None
    cover = request.files.get("cover")
    if cover and cover.filename:
        ext = Path(cover.filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            flash("Geçersiz kapak dosyası.")
            return redirect(url_for("search"))
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in title).strip("-")[:80]
        destination = Path("static/kapaklar") / f"{safe_name}{ext}"
        cover.save(destination)
        cover_path = f"/static/kapaklar/{destination.name}"
    created, book_id = db.add_book({
        "title": title,
        "author": request.form.get("author"),
        "publisher": request.form.get("publisher"),
        "cover_path": cover_path,
        "page_count": page_count,
        "isbn": request.form.get("isbn"),
        "publish_year": request.form.get("publish_year"),
        "status": request.form.get("status", "Okunacak"),
    })
    flash("Kitap kitaplığınıza eklendi." if created else "Bu kitap zaten kitaplığında.")
    return redirect(url_for("library", raf=request.form.get("status", "Okunacak")))


@app.route("/book/<int:book_id>/status", methods=["POST"])
@login_required
def change_status(book_id: int):
    success, message = db.update_status(book_id, request.form.get("status", ""))
    flash(message)
    return redirect(request.referrer or url_for("library"))


@app.route("/book/<int:book_id>/read", methods=["POST"])
@login_required
def read_today(book_id: int):
    conn = db.get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        conn.close()
        flash("Kitap bulunamadı.")
        return redirect(url_for("library"))
    try:
        page = int(request.form.get("page", "0"))
    except ValueError:
        page = 0
    error = validate_page_read(book, page, book["current_page"])
    if error:
        conn.close()
        flash(error)
        return redirect(request.referrer or url_for("library"))
    conn.execute("INSERT INTO reading_logs(book_id, page, log_date) VALUES (?, ?, ?)", (book_id, page, today()))
    conn.execute("UPDATE books SET current_page = ?, status = 'Okunuyor', started_at = COALESCE(started_at, ?) WHERE id = ?", (page, today(), book_id))
    if book["page_count"] and page >= book["page_count"]:
        conn.execute("UPDATE books SET status = 'Okundu', finished_at = ? WHERE id = ?", (today(), book_id))
    conn.commit()
    conn.close()
    flash("Bugünkü okuma kaydedildi.")
    return redirect(request.referrer or url_for("library"))


@app.route("/book/<int:book_id>/review", methods=["POST"])
@login_required
def update_review(book_id: int):
    rating = int(request.form.get("rating") or 0)
    if rating not in range(0, 6):
        flash("Puan 1-5 arasında olmalıdır.")
        return redirect(request.referrer or url_for("library"))
    conn = db.get_db()
    conn.execute("UPDATE books SET rating = ?, notes = ? WHERE id = ?", (rating or None, request.form.get("notes", "").strip(), book_id))
    conn.commit()
    conn.close()
    flash("Puan ve not kaydedildi.")
    return redirect(request.referrer or url_for("library"))


@app.route("/book/<int:book_id>/delete", methods=["POST"])
@login_required
def delete_book(book_id: int):
    conn = db.get_db()
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()
    flash("Kitap kitaplıktan kaldırıldı.")
    return redirect(request.referrer or url_for("library"))


@app.route("/log/<int:log_id>/delete", methods=["POST"])
@login_required
def delete_log(log_id: int):
    conn = db.get_db()
    log = conn.execute("SELECT * FROM reading_logs WHERE id = ?", (log_id,)).fetchone()
    if log:
        conn.execute("DELETE FROM reading_logs WHERE id = ?", (log_id,))
        remaining = conn.execute(
            "SELECT page FROM reading_logs WHERE book_id = ? ORDER BY log_date DESC, id DESC LIMIT 1",
            (log["book_id"],),
        ).fetchone()
        new_page = remaining["page"] if remaining else 0
        conn.execute("UPDATE books SET current_page = ? WHERE id = ?", (new_page, log["book_id"]))
    conn.commit()
    conn.close()
    flash("Son okuma kaydı silindi.")
    return redirect(request.referrer or url_for("library"))


@app.route("/stats")
@login_required
def stats():
    conn = db.get_db()
    rows = conn.execute(
        """
        SELECT substr(log_date,1,7) AS month, SUM(page_delta) AS pages
        FROM (
            SELECT log_date,
                   page - COALESCE(LAG(page) OVER (PARTITION BY book_id ORDER BY log_date, id), 0) AS page_delta
            FROM reading_logs
        ) WHERE substr(log_date,1,4) = ?
        GROUP BY month ORDER BY month
        """,
        (str(datetime.now(ISTANBUL).year),),
    ).fetchall()
    finished = conn.execute(
        "SELECT COUNT(*) AS count FROM books WHERE status = 'Okundu' AND substr(finished_at,1,4) = ?",
        (str(datetime.now(ISTANBUL).year),),
    ).fetchone()["count"]
    conn.close()
    count, goal, goal_status = yearly_goal_status()
    streak = calculate_streak()
    labels = [f"{month}-{calendar.month_abbr[int(month.split('-')[1])]}" for month in (row["month"] for row in rows)]
    pages = [max(row["pages"], 0) for row in rows]
    return render_template("stats.html", count=count, goal=goal, goal_status=goal_status,
                           finished=finished, labels=labels, pages=pages, streak=streak)


def calculate_streak() -> int:
    conn = db.get_db()
    rows = conn.execute("SELECT DISTINCT log_date FROM reading_logs ORDER BY log_date DESC").fetchall()
    conn.close()
    if not rows:
        return 0
    streak = 0
    cursor = datetime.now(ISTANBUL).date()
    dates = {date.fromisoformat(row["log_date"]) for row in rows}
    if cursor not in dates:
        cursor -= timedelta(days=1)
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = db.get_db()
    if request.method == "POST":
        try:
            goal = int(request.form.get("yearly_goal", "24"))
            if goal < 1:
                raise ValueError
        except ValueError:
            conn.close()
            flash("Yıllık hedef 1 veya daha büyük olmalıdır.")
            return redirect(url_for("settings"))
        conn.execute("INSERT INTO settings(key, value) VALUES ('yearly_goal', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(goal),))
        conn.commit()
        conn.close()
        flash("Ayarlar kaydedildi.")
        return redirect(url_for("settings"))
    row = conn.execute("SELECT value FROM settings WHERE key = 'yearly_goal'").fetchone()
    conn.close()
    return render_template("settings.html", yearly_goal=int(row["value"]) if row else 24)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)
