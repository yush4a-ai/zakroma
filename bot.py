"""Закрома — Telegram-бот. Запуск: python bot.py"""
import asyncio
import html
import io
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import ai
import config
import core
import db
import extract
import importer
import media
import secrets
import web

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
    "/del 12 — убрать запись №12 в мусор\n"
    "/cleantags — навести порядок в тегах\n\n"
    "📦 <b>Импорт:</b> пришли result.json (экспорт Избранного из Telegram) "
    "или архив/message_1.json из Instagram"
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

async def save(m: Message, *, kind, url=None, title="", note="", text="", image=None,
               image_url=None):
    uid = m.from_user.id
    await db.ensure_user(uid, m.from_user.username)

    if url:
        dup = await db.find_by_url(uid, url)
        if dup and dup["status"] != "trash":
            return await m.answer("Это уже есть в закромах 👇\n\n" + card(dup),
                                  disable_web_page_preview=True)

    source = None
    origin = m.forward_origin
    if origin is not None:
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_user", None)
        source = getattr(chat, "title", None) or getattr(chat, "full_name", None) or "переслано"

    row = await core.process_item(uid, kind=kind, url=url, title=title, note=note, text=text,
                                  image=image, source=source, image_url=image_url)
    await m.answer("📥 Сохранил\n\n" + card(row), disable_web_page_preview=True)


# ---------- импорт старых завалов ----------

pending_threads: dict[int, list[dict]] = {}
pending_tagmap: dict[int, dict] = {}


async def start_import(bot: Bot, chat_id: int, uid: int, entries: list[dict]) -> bool:
    return await importer.start(bot, chat_id, uid, entries)


@router.message(lambda m: bool(m.document) and
                (m.document.file_name or "").lower().endswith((".json", ".zip")))
async def import_file(m: Message, bot: Bot):
    if not allowed(m):
        return
    doc = m.document
    if (doc.file_size or 0) > 20 * 1024 * 1024:
        return await m.answer(
            "Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. "
            "Распакуй архив и пришли только message_1.json из папки нужной переписки.")
    await db.ensure_user(m.from_user.id, m.from_user.username)
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        kind, data = importer.detect(buf.getvalue(), doc.file_name)
    except Exception as e:
        return await m.answer(f"Не смог прочитать файл 😕 ({esc(e)})")

    if kind == "instagram_zip":
        if not data:
            return await m.answer("В архиве не нашёл переписок со ссылками.")
        if len(data) == 1:
            entries = data[0]["entries"]
        else:
            pending_threads[m.from_user.id] = data[:6]
            kb = InlineKeyboardBuilder()
            for i, t in enumerate(data[:6]):
                n = sum(1 for e in t["entries"] if e["url"])
                kb.button(text=f"{t['name'][:30]} — {n} ссылок", callback_data=f"imp:{i}")
            kb.adjust(1)
            return await m.answer("Из какой переписки импортировать?", reply_markup=kb.as_markup())
    else:
        entries = data

    if not entries:
        return await m.answer("В файле не нашёл ни ссылок, ни заметок.")
    if not await start_import(bot, m.chat.id, m.from_user.id, entries):
        await m.answer("Импорт уже идёт. Дождись или останови: /stop_import")


@router.callback_query(F.data.startswith("imp:"))
async def import_choose(c: CallbackQuery, bot: Bot):
    threads = pending_threads.pop(c.from_user.id, None)
    if not threads:
        return await c.answer("Устарело, пришли файл ещё раз")
    t = threads[int(c.data.split(":")[1])]
    await c.message.edit_text(f"Импортирую переписку «{esc(t['name'])}»")
    if not await start_import(bot, c.message.chat.id, c.from_user.id, t["entries"]):
        await c.message.answer("Импорт уже идёт. Дождись или останови: /stop_import")
    await c.answer()


@router.message(Command("stop_import"))
async def stop_import(m: Message):
    if not allowed(m):
        return
    task = importer.running.get(m.from_user.id)
    if not task:
        return await m.answer("Сейчас ничего не импортируется.")
    importer.stopping[m.from_user.id] = True
    task.cancel()


@router.message(Command("login"))
async def login(m: Message):
    if not allowed(m):
        return
    await db.ensure_user(m.from_user.id, m.from_user.username)
    code = f"{secrets.randbelow(10**6):06d}"
    await db.save_login_code(m.from_user.id, code)
    await m.answer(f"Код для входа в программу или на сайт:\n\n<code>{code}</code>\n\n"
                   f"Действует 10 минут, сработает один раз.")


# ---------- уборка тегов ----------

CLEAN_PROMPT = """Вот теги из личной базы полезных материалов (тег — сколько записей):
{tags}

Наведи порядок: объедини синонимы и слишком мелкие теги в понятные группы,
чтобы в итоге осталось примерно 15–25 тегов. Теги — строчные, по-русски, слова через _.
Названия программ и брендов (unity, blender, chatgpt) можно оставить латиницей.

Ответь ТОЛЬКО JSON: {{"map": {{"старый_тег": "новый_тег", ...}}}}
Включай в map только те теги, которые меняются."""


@router.message(Command("cleantags"))
async def cleantags(m: Message):
    if not allowed(m):
        return
    t = await db.user_tags(m.from_user.id)
    if len(t) < 5:
        return await m.answer("Тегов пока мало, убирать нечего.")
    await bot_typing(m)
    data = await ai._ask_text(CLEAN_PROMPT.format(tags="\n".join(f"{n} — {c}" for n, c in t)))
    current = {n for n, _ in t}
    mapping = {}
    for old, new in (data.get("map") or {}).items():
        new = ai._clean_tags([new])
        if old in current and new and new[0] != old:
            mapping[old] = new[0]
    if not mapping:
        return await m.answer("Теги и так в порядке 👌")

    groups: dict[str, list[str]] = {}
    for old, new in mapping.items():
        groups.setdefault(new, []).append(old)
    lines = [f"#{new} ← " + ", ".join(f"#{o}" for o in olds) for new, olds in groups.items()]
    final = sorted({mapping.get(n, n) for n in current})
    pending_tagmap[m.from_user.id] = mapping

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Применить", callback_data="tags:yes")
    kb.button(text="Отмена", callback_data="tags:no")
    await m.answer("Предлагаю объединить:\n" + "\n".join(lines[:40]) +
                   f"\n\nВ итоге останется {len(final)} тегов.", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("tags:"))
async def cleantags_apply(c: CallbackQuery):
    mapping = pending_tagmap.pop(c.from_user.id, None)
    if c.data == "tags:no" or not mapping:
        await c.message.edit_text("Оставил теги как есть.")
        return await c.answer()
    n = await db.remap_tags(c.from_user.id, mapping)
    await c.message.edit_text(f"✅ Готово, обновил {n} записей. Список тегов: /tags")
    await c.answer()


async def bot_typing(m: Message):
    try:
        await m.bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass


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


@router.message(F.video | F.video_note | F.document.mime_type.startswith("video/"))
async def video_file(m: Message, bot: Bot):
    if not allowed(m):
        return
    file = m.video or m.video_note or m.document
    if (file.file_size or 0) > 20 * 1024 * 1024:
        return await m.answer("Видео больше 20 МБ: Telegram не даёт ботам скачивать такие. "
                              "Кинь лучше ссылку на него.")
    status = await m.answer("🎬 Смотрю видео… это может занять до минуты")
    buf = io.BytesIO()
    await bot.download(file, destination=buf)
    speech = await media.from_bytes(buf.getvalue())
    caption = m.caption or ""
    urls = extract.find_urls(caption)
    text = "\n".join(x for x in [caption, f"Речь в видео: {speech}" if speech else ""] if x)
    await save(m, kind="video", url=urls[0] if urls else None, note=caption, text=text)
    try:
        await status.delete()
    except Exception:
        pass


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
        dup = await db.find_by_url(m.from_user.id, u)
        if dup and dup["status"] != "trash":
            await m.answer("Это уже есть в закромах 👇\n\n" + card(dup),
                           disable_web_page_preview=True)
            continue

        status = None
        if media.is_video_url(u):
            status = await m.answer("🎬 Смотрю видео… это может занять до минуты")
        kind, title, text, image_url = await core.enrich_url(u)
        await save(m, kind=kind, url=u, title=title, note=note, text=text, image_url=image_url)
        if status:
            try:
                await status.delete()
            except Exception:
                pass


async def main():
    if not config.ALLOWED_USERS:
        logging.warning("ALLOWED_USERS пуст — бот никому не ответит, кроме подсказки с ID.")
    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await web.start(bot)
    await importer.resume_all(bot)
    logging.info("Закрома запущены")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
