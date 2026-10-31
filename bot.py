"""Закрома — Telegram-бот. Запуск: python bot.py"""
import asyncio
import html
import io
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

import ai
import config
import db
import extract

logging.basicConfig(level=logging.INFO)
router = Router()

HELP = (
    "<b>Закрома</b> — сюда скидывай всё полезное.\n\n"
    "📥 <b>Сохранить:</b> ссылку (сайт, YouTube, рилс), заметку, голосовое или скриншот. "
    "Можно переслать пост из канала. К ссылке можно дописать пару слов — так потом проще найти.\n\n"
    "🔎 <b>Найти:</b> начни с <code>?</code>, например <code>? нейронка для субтитров</code>\n\n"
    "/last — последние 10\n"
    "/tags — все теги\n"
    "/tag монтаж — всё по тегу\n"
    "/del 12 — убрать запись №12 в мусор"
)

STATUS_ICON = {"new": "", "try": "🧪 ", "used": "✅ "}


def esc(s) -> str:
    return html.escape(str(s or ""))


def card(r, short=False) -> str:
    tags = " ".join(f"#{t}" for t in (r["tags"] or []))
    head = f"{STATUS_ICON.get(r['status'], '')}<b>{esc(r['title'])}</b> <i>№{r['id']}</i>"
    lines = [head]
    if r["summary"] and not short:
        lines.append(esc(r["summary"]))
    if r["url"]:
        lines.append(esc(r["url"]))
    if tags:
        lines.append(tags)
    return "\n".join(lines)


def allowed(m: Message) -> bool:
    return m.from_user and m.from_user.id in config.ALLOWED_USERS


# ---------- команды ----------

@router.message(CommandStart())
async def start(m: Message):
    if not allowed(m):
        return await m.answer(f"Это личный бот. Твой ID: <code>{m.from_user.id}</code>")
    await db.ensure_user(m.from_user.id, m.from_user.username)
    await m.answer(HELP)


@router.message(Command("help"))
async def help_(m: Message):
    if allowed(m):
        await m.answer(HELP)


@router.message(Command("last"))
async def last(m: Message):
    if not allowed(m):
        return
    rows = await db.last_items(m.from_user.id)
    if not rows:
        return await m.answer("Пока пусто. Кинь сюда первую ссылку!")
    total = await db.count(m.from_user.id)
    await m.answer(f"Всего в закромах: {total}\n\n" + "\n\n".join(card(r, short=True) for r in rows),
                   disable_web_page_preview=True)


@router.message(Command("tags"))
async def tags(m: Message):
    if not allowed(m):
        return
    t = await db.user_tags(m.from_user.id)
    if not t:
        return await m.answer("Тегов пока нет.")
    await m.answer("\n".join(f"#{name} — {c}" for name, c in t))


@router.message(Command("tag"))
async def tag(m: Message, command: CommandObject):
    if not allowed(m):
        return
    name = (command.args or "").strip().lstrip("#").lower().replace(" ", "_")
    if not name:
        return await m.answer("Напиши так: /tag монтаж")
    rows = await db.by_tag(m.from_user.id, name)
    if not rows:
        return await m.answer(f"По тегу #{esc(name)} ничего нет.")
    await m.answer("\n\n".join(card(r, short=True) for r in rows), disable_web_page_preview=True)


@router.message(Command("del"))
async def delete(m: Message, command: CommandObject):
    if not allowed(m):
        return
    try:
        item_id = int((command.args or "").strip().lstrip("№"))
    except ValueError:
        return await m.answer("Напиши так: /del 12")
    ok = await db.set_status(m.from_user.id, item_id, "trash")
    await m.answer("🗑 Убрал в мусор." if ok else "Такой записи нет.")


# ---------- поиск ----------

@router.message(F.text.startswith("?"))
async def search(m: Message):
    if not allowed(m):
        return
    q = m.text.lstrip("?").strip()
    if not q:
        return await m.answer("Что ищем? Например: <code>? как убрать фон с видео</code>")
    qv = await ai.embed(q)
    ids = ai.rank(qv, await db.all_embeddings(m.from_user.id))
    if not ids:
        return await m.answer("Ничего похожего не нашёл 🤷")
    rows = await db.get_items(m.from_user.id, ids)
    await m.answer("🔎 Нашёл:\n\n" + "\n\n".join(card(r) for r in rows),
                   disable_web_page_preview=True)


# ---------- сохранение ----------

async def save(m: Message, *, kind, url=None, title="", note="", text="", image=None):
    uid = m.from_user.id
    await db.ensure_user(uid, m.from_user.username)

    if url:
        dup = await db.find_by_url(uid, url)
        if dup and dup["status"] != "trash":
            return await m.answer("Это уже есть в закромах 👇\n\n" + card(dup),
                                  disable_web_page_preview=True)

    existing = [t for t, _ in await db.user_tags(uid)]
    res = await ai.analyze(kind, url, title, note, text, existing, image=image)

    source = None
    origin = m.forward_origin
    if origin is not None:
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_user", None)
        source = getattr(chat, "title", None) or getattr(chat, "full_name", None) or "переслано"

    emb_text = " ".join(x for x in [res["title"], res["summary"], " ".join(res["tags"]),
                                    note, text[:1000]] if x)
    item_id = await db.add_item(
        uid, kind=kind, url=url, title=res["title"], raw_text=text, note=note,
        summary=res["summary"], tags=res["tags"], source=source,
        embedding=await ai.embed(emb_text),
    )
    row = {"id": item_id, "title": res["title"], "summary": res["summary"],
           "url": url, "tags": res["tags"], "status": "new"}
    await m.answer("📥 Сохранил\n\n" + card(row), disable_web_page_preview=True)


@router.message(F.voice | F.audio)
async def voice(m: Message, bot: Bot):
    if not allowed(m):
        return
    if not config.GROQ_API_KEY:
        return await m.answer("Голосовые пока выключены: нужен ключ GROQ_API_KEY в настройках.")
    await bot.send_chat_action(m.chat.id, "typing")
    buf = io.BytesIO()
    await bot.download(m.voice or m.audio, destination=buf)
    text = await ai.transcribe(buf.getvalue())
    if not text:
        return await m.answer("Не смог разобрать голосовое 😕")
    await save(m, kind="voice", note=text, text=text)


@router.message(F.photo)
async def photo(m: Message, bot: Bot):
    if not allowed(m):
        return
    await bot.send_chat_action(m.chat.id, "typing")
    buf = io.BytesIO()
    await bot.download(m.photo[-1], destination=buf)
    caption = m.caption or ""
    urls = extract.find_urls(caption)
    await save(m, kind="photo", url=urls[0] if urls else None, note=caption,
               text=caption, image=buf.getvalue())


@router.message(F.text | F.caption)
async def text_msg(m: Message, bot: Bot):
    if not allowed(m):
        return
    await bot.send_chat_action(m.chat.id, "typing")
    body = m.text or m.caption or ""
    urls = extract.find_urls(body)
    # ссылки, спрятанные под текстом (text_link)
    for e in (m.entities or m.caption_entities or []):
        if e.type == "text_link" and e.url and e.url not in urls:
            urls.append(e.url)

    if not urls:
        return await save(m, kind="note", note=body, text=body)

    note = body
    for u in urls:
        note = note.replace(u, "")
    note = note.strip()

    for u in urls[:5]:
        data = await extract.fetch(u)
        await save(m, kind=extract.detect_kind(u), url=u, title=data["title"],
                   note=note, text=data["text"])


async def main():
    if not config.ALLOWED_USERS:
        logging.warning("ALLOWED_USERS пуст — бот никому не ответит, кроме подсказки с ID.")
    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("Закрома запущены")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
