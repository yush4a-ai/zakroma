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
