"""Общая логика сохранения: используется ботом, импортом и программой."""
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
    return {"id": item_id, "title": res["title"], "summary": res["summary"],
            "url": url, "tags": res["tags"], "status": "new"}


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
