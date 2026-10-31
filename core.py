"""Общая логика сохранения: используется ботом, импортом и программой."""
import asyncio
import io
import re
import secrets

import httpx

import ai
import db
import extract
import media


async def enrich_url(url: str) -> tuple[str, str, str, str | None]:
    """Достаёт содержимое по ссылке. Возвращает (kind, title, text, image_url)."""
    kind = extract.detect_kind(url)
    data = await extract.fetch(url)
    if media.is_video_url(url):
        v = await media.from_url(url)
        title = v["title"] or data["title"]
        text = "\n".join(x for x in [v["text"], data["text"]] if x)
        return kind, title, text, v.get("image") or data.get("image")
    return kind, data["title"], data["text"], data.get("image")


async def process_item(uid: int, *, kind, url=None, title="", note="", text="", image=None,
                       source=None, ext_id=None, created_at=None, image_url=None) -> dict:
    """ИИ-разбор + сохранение в базу. Возвращает карточку записи."""
    existing = [t for t, _ in await db.user_tags(uid)]
    res = await ai.analyze(kind, url, title, note, text, existing, image=image)

    emb_text = " ".join(x for x in [res["title"], res["summary"], " ".join(res["tags"]),
                                    note, (text or "")[:1500]] if x)
    item_id = await db.add_item(
        uid, kind=kind, url=url, title=res["title"], raw_text=text, note=note,
        summary=res["summary"], tags=res["tags"], source=source,
        embedding=await ai.embed(emb_text), ext_id=ext_id, created_at=created_at,
        image_url=image_url,
    )
    if image:
        asyncio.create_task(store_thumb(item_id, data=image))
    elif image_url or (url and "youtu" in url):
        asyncio.create_task(store_thumb(item_id, url=image_url or url))
    return {"id": item_id, "title": res["title"], "summary": res["summary"],
            "url": url, "tags": res["tags"], "status": "new"}


# ---------- обложки ----------

YT_ID = re.compile(r"(?:v=|shorts/|youtu\.be/|embed/)([\w-]{11})")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def _shrink(data: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((720, 720))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


async def store_thumb(item_id: int, url: str | None = None, data: bytes | None = None) -> bool:
    """Скачивает обложку, уменьшает и сохраняет в базу. url — картинка или ссылка на страницу."""
    try:
        if data is None and url:
            m = YT_ID.search(url) if "youtu" in url else None
            img_url = f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg" if m else url
            async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=20) as c:
                r = await c.get(img_url)
                if "image" not in r.headers.get("content-type", ""):
                    page = await extract.fetch(url)
                    if not page.get("image"):
                        await db.thumb_failed(item_id)
                        return False
                    r = await c.get(page["image"])
                data = r.content if r.status_code == 200 else None
        if not data:
            await db.thumb_failed(item_id)
            return False
        small = await asyncio.to_thread(_shrink, data)
        await db.save_thumb(item_id, secrets.token_urlsafe(16), small)
        return True
    except Exception as e:
        print("thumb error:", item_id, str(e)[:150])
        await db.thumb_failed(item_id)
        return False


async def thumbs_loop():
    """Докачивает обложки старым записям."""
    while True:
        try:
            rows = await db.items_without_thumbs(10)
            for r in rows:
                await store_thumb(r["id"], url=r["image_url"] or r["url"])
                await asyncio.sleep(2)
            await asyncio.sleep(30 if rows else 600)
        except Exception as e:
            print("thumbs loop error:", e)
            await asyncio.sleep(120)


CLEAN_PROMPT = """Вот теги из личной базы полезных материалов (тег — сколько записей):
{tags}

Наведи порядок: объедини синонимы и слишком мелкие теги в понятные группы,
чтобы в итоге осталось примерно 15–25 тегов. Теги — строчные, по-русски, слова через _.
Названия программ и брендов (unity, blender, chatgpt) можно оставить латиницей.

Ответь ТОЛЬКО JSON: {{"map": {{"старый_тег": "новый_тег", ...}}}}
Включай в map только те теги, которые меняются."""


async def suggest_tag_cleanup(uid: int) -> dict[str, str]:
    """ИИ предлагает, какие теги объединить. Возвращает {старый: новый}."""
    t = await db.user_tags(uid)
    if len(t) < 5:
        return {}
    data = await ai._ask_text(CLEAN_PROMPT.format(tags="\n".join(f"{n} — {c}" for n, c in t)))
    current = {n for n, _ in t}
    mapping = {}
    for old, new in (data.get("map") or {}).items():
        new = ai._clean_tags([new])
        if old in current and new and new[0] != old:
            mapping[old] = new[0]
    return mapping


async def backfill_loop():
    """Тихо дорабатывает записи, которым не досталась выжимка (например, ИИ упёрся в лимит)."""
    import asyncio
    import importer
    while True:
        await asyncio.sleep(120)
        if importer.running:
            continue
        try:
            rows = await db.pool.fetch(
                "SELECT id, user_id, kind, url, title, note, raw_text FROM items "
                "WHERE coalesce(summary, '') = '' AND status <> 'trash' AND kind NOT IN ('note','voice') "
                "ORDER BY id DESC LIMIT 5")
            for r in rows:
                existing = [t for t, _ in await db.user_tags(r["user_id"])]
                res = await ai.analyze(r["kind"], r["url"], r["title"], r["note"], r["raw_text"] or "", existing)
                if not res["summary"]:
                    continue
                emb_text = " ".join(x for x in [res["title"], res["summary"], " ".join(res["tags"]),
                                                r["note"] or "", (r["raw_text"] or "")[:1500]] if x)
                await db.pool.execute(
                    "UPDATE items SET title=$2, summary=$3, tags=$4, embedding=$5 WHERE id=$1",
                    r["id"], res["title"], res["summary"], res["tags"], await ai.embed(emb_text))
                await asyncio.sleep(10)
        except Exception as e:
            print("backfill error:", e)
