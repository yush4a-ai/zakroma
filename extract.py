"""Достаём содержимое по ссылке: сайты, YouTube, Instagram (по возможности)."""
import re

import httpx
from bs4 import BeautifulSoup

URL_RE = re.compile(r"https?://\S+")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "ru,en;q=0.8",
}


def find_urls(text: str) -> list[str]:
    return [u.rstrip(").,!?»\"'") for u in URL_RE.findall(text or "")]


def detect_kind(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u or "tiktok.com" in u:
        return "reel"
    return "link"


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


async def fetch(url: str) -> dict:
    """Возвращает {'title': ..., 'text': ...}. Никогда не падает — в худшем случае пусто."""
    kind = detect_kind(url)
    out = {"title": "", "text": "", "image": None}
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as c:
            if kind == "youtube":
                r = await c.get("https://www.youtube.com/oembed",
                                params={"url": url, "format": "json"})
                if r.status_code == 200:
                    j = r.json()
                    out["title"] = j.get("title", "")
                    out["text"] = f"Канал: {j.get('author_name', '')}"

            r = await c.get(url)
            soup = BeautifulSoup(r.text, "html.parser")
            out["title"] = out["title"] or _meta(soup, "og:title", "twitter:title") or (
                soup.title.string.strip() if soup.title and soup.title.string else "")
            desc = _meta(soup, "og:description", "description", "twitter:description")
            out["image"] = _meta(soup, "og:image", "twitter:image") or None

            body = ""
            if kind == "link":
                for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                    t.decompose()
                body = " ".join(soup.get_text(" ").split())[:4000]

            out["text"] = "\n".join(x for x in [out["text"], desc, body] if x)
    except Exception:
        pass
    return out
