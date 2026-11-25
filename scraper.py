import re
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


BASE_URL = "https://www.kitapyurdu.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def search_books(query: str) -> tuple[list[dict], str | None]:
    url = f"{BASE_URL}/index.php?route=product/search&filter_name={quote(query)}"
    try:
        response = _session().get(url, timeout=5)
        response.raise_for_status()
    except requests.RequestException:
        return [], "Şu an kitap aranamıyor, elle ekleyebilirsin."

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select(".ky-product")
    if not cards:
        return [], "Kitap bulunamadı, elle ekleyebilirsin."

    results = []
    for card in cards[:24]:
        title_link = card.select_one("a.ky-product-title")
        if not title_link:
            continue
        href = title_link.get("href", "")
        if href and href.startswith("/"):
            href = BASE_URL + href
        author_link = card.select_one(".ky-product-author a")
        publisher_link = card.select_one(".ky-product-publisher a")
        cover = card.select_one("a.ky-product-cover img")
        cover_url = None
        if cover:
            cover_url = cover.get("data-src") or cover.get("src") or cover.get("data-original")
            if cover_url and cover_url.startswith("/"):
                cover_url = BASE_URL + cover_url
        results.append(
            {
                "title": title_link.get_text(" ", strip=True),
                "author": author_link.get_text(" ", strip=True) if author_link else "",
                "publisher": publisher_link.get_text(" ", strip=True) if publisher_link else "",
                "cover_url": cover_url,
                "source_url": href,
            }
        )
    return results, None if results else "Kitap bulunamadı, elle ekleyebilirsin."


def get_book_details(source_url: str) -> dict:
    details: dict = {}
    try:
        response = _session().get(source_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for item in soup.select(".ky-pd-attributes__item"):
            text = item.get_text(" ", strip=True)
            if "Sayfa Sayısı:" in text:
                match = re.search(r"(\d+)", text)
                if match:
                    details["page_count"] = int(match.group(1))
            elif "ISBN:" in text:
                details["isbn"] = re.sub(r"\D", "", text)
            elif "Yayın Yılı:" in text:
                match = re.search(r"\d{4}", text)
                if match:
                    details["publish_year"] = match.group()
    except requests.RequestException:
        pass
    return details


def download_cover(url: str | None, title: str) -> str | None:
    if not url:
        return None
    cover_dir = Path("static") / "kapaklar"
    cover_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:80] or "kapak"
    filename = f"{safe_name}.jpg"
    destination = cover_dir / filename
    try:
        response = _session().get(url, timeout=5)
        response.raise_for_status()
        if not response.content:
            return None
        destination.write_bytes(response.content)
        return f"/static/kapaklar/{filename}"
    except requests.RequestException:
        return None
