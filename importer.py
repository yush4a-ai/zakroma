"""Импорт старых завалов: Избранное Telegram (result.json) и переписка Instagram (message_*.json / zip)."""
import asyncio
import io
import json
import re
import zipfile
from datetime import datetime, timezone

import core
import db
import extract

URL_RE = re.compile(r"https?://\S+")


def _dt_from_unix(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None


def _entries_from_text(ext_prefix: str, text: str, urls: list[str], created_at, source=None):
    """Одно сообщение → одна или несколько записей (по записи на каждую ссылку)."""
    urls = [u.rstrip(").,!?»\"'") for u in urls if u]
    urls = list(dict.fromkeys(urls))
    note = text
    for u in urls:
        note = note.replace(u, "")
    note = " ".join(note.split())
    out = []
    for i, u in enumerate(urls[:5]):
        out.append({"ext_id": f"{ext_prefix}:{i}", "url": u, "note": note, "text": "",
                    "created_at": created_at, "source": source})
    if not urls and len(note) >= 3:
        out.append({"ext_id": ext_prefix, "url": None, "note": note, "text": "",
                    "created_at": created_at, "source": source})
    return out


# ---------- Telegram ----------

def parse_telegram(data: dict) -> list[dict]:
    out = []
    for msg in data.get("messages", []):
        if msg.get("type") != "message":
            continue
        ents = msg.get("text_entities") or []
        if ents:
            text = "".join(e.get("text", "") for e in ents)
            urls = [e.get("text") for e in ents if e.get("type") == "link"]
            urls += [e.get("href") for e in ents if e.get("type") == "text_link"]
        else:
            raw = msg.get("text", "")
            text = raw if isinstance(raw, str) else "".join(
                p if isinstance(p, str) else p.get("text", "") for p in raw)
            urls = URL_RE.findall(text)
        source = msg.get("forwarded_from")
        out += _entries_from_text(f"tg:{msg.get('id')}", text, urls,
                                  _dt_from_unix(msg.get("date_unixtime")), source)
    return out


# ---------- Instagram ----------

def _fix(s: str) -> str:
    """Instagram кладёт кириллицу в JSON «кракозябрами» — чиним."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def parse_instagram(data: dict) -> list[dict]:
    out = []
    for msg in data.get("messages", []):
        ts = msg.get("timestamp_ms", 0)
        created = _dt_from_unix(ts // 1000) if ts else None
        share = msg.get("share") or {}
        content = _fix(msg.get("content", "") or "")
        if share.get("link"):
            caption = _fix(share.get("share_text", "") or "")
            owner = _fix(share.get("original_content_owner", "") or "")
            note = "" if content.startswith(("Вы отправили", "You sent", "sent an attachment")) else content
            out.append({"ext_id": f"ig:{ts}", "url": share["link"], "note": note,
                        "text": "\n".join(x for x in [f"Автор: {owner}" if owner else "",
                                                      f"Подпись: {caption}" if caption else ""] if x),
                        "created_at": created, "source": owner or None})
        elif content and not content.startswith(("Вы отправили", "You sent", "sent an attachment")):
            out += _entries_from_text(f"ig:{ts}", content, URL_RE.findall(content), created)
    return out


def instagram_threads_from_zip(blob: bytes) -> list[dict]:
    """Все переписки в архиве Instagram: [{'name', 'entries'}], отсортированы по числу ссылок."""
    threads: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            m = re.search(r"messages/(?:inbox|message_requests)/([^/]+)/message_\d+\.json$", name)
            if not m:
                continue
            data = json.loads(z.read(name))
            t = threads.setdefault(m.group(1), {"title": "", "entries": []})
            parts = [_fix(p.get("name", "")) for p in data.get("participants", [])]
            t["title"] = _fix(data.get("title", "")) or ", ".join(parts)
            t["entries"] += parse_instagram(data)
    res = [{"name": v["title"] or k, "entries": v["entries"]} for k, v in threads.items()]
    res = [r for r in res if any(e["url"] for e in r["entries"])]
    return sorted(res, key=lambda r: -sum(1 for e in r["entries"] if e["url"]))


def detect(blob: bytes, filename: str):
    """Определяет тип файла. Возвращает ('telegram'|'instagram', entries) или ('instagram_zip', threads)."""
    if filename.lower().endswith(".zip"):
        return "instagram_zip", instagram_threads_from_zip(blob)
    data = json.loads(blob.decode("utf-8"))
    if "participants" in data:
        return "instagram", parse_instagram(data)
    if "messages" in data:
        return "telegram", parse_telegram(data)
    raise ValueError("Не узнаю этот файл")


# ---------- сам импорт ----------

running: dict[int, asyncio.Task] = {}


async def run(bot, chat_id: int, uid: int, entries: list[dict]):
    total = len(entries)
    status = await bot.send_message(chat_id, f"📦 Импорт: найдено {total} записей. Начинаю…\n"
                                             f"Остановить: /stop_import")
    done = added = skipped = errors = 0
    try:
        for e in entries:
            done += 1
            try:
                if await db.ext_exists(uid, e["ext_id"]):
                    skipped += 1
                    continue
                if e["url"]:
                    dup = await db.find_by_url(uid, e["url"])
                    if dup:
                        skipped += 1
                        continue
                    kind, title, text = await core.enrich_url(e["url"])
                    text = "\n".join(x for x in [e["text"], text] if x)
                else:
                    kind, title, text = "note", "", e["note"]
                await core.process_item(uid, kind=kind, url=e["url"], title=title, note=e["note"],
                                        text=text, source=e["source"], ext_id=e["ext_id"],
                                        created_at=e["created_at"])
                added += 1
            except Exception as ex:
                errors += 1
                print("import error:", e.get("url"), ex)

            if done % 10 == 0:
                try:
                    await status.edit_text(f"📦 Импорт: {done}/{total}\n"
                                           f"добавлено {added}, уже было {skipped}, ошибок {errors}\n"
                                           f"Остановить: /stop_import")
                except Exception:
                    pass
            # бережём бесплатные лимиты ИИ и не злим Instagram
            await asyncio.sleep(3 if e["url"] and extract.detect_kind(e["url"]) == "reel" else 1.5)
    except asyncio.CancelledError:
        await bot.send_message(chat_id, f"⏹ Импорт остановлен на {done}/{total}. "
                                        f"Добавлено {added}. Можно отправить файл ещё раз — "
                                        f"продолжу с того же места.")
        raise
    finally:
        running.pop(uid, None)

    await bot.send_message(
        chat_id,
        f"✅ Импорт готов!\nДобавлено: {added}\nУже было: {skipped}\nОшибок: {errors}\n\n"
        f"Теперь наведём порядок в тегах: /cleantags",
    )
