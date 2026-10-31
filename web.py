"""API и веб-интерфейс Закромов. Работает в том же процессе, что и бот."""
import asyncio
import os
import re
import secrets
from pathlib import Path

from aiohttp import web

import ai
import core
import db
import extract

STATIC = Path(__file__).parent / "static"
BOT_USERNAME = "vzakroma_bot"   # уточняется при старте из Telegram
KIND_GROUPS = {
    "reel": ["reel"],
    "youtube": ["youtube"],
    "link": ["link"],
    "note": ["note", "voice"],
    "media": ["photo", "video"],
}
YT_ID = re.compile(r"(?:v=|shorts/|youtu\.be/|embed/)([\w-]{11})")
WORD = re.compile(r"[\wё]+", re.I)


def _thumb(r) -> str | None:
    if r["thumb_key"]:
        return f"/img/{r['thumb_key']}.jpg"
    if r["url"] and "youtu" in r["url"]:
        m = YT_ID.search(r["url"])
        if m:
            return f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
    return r["image_url"]


def _item(r, full: bool = False) -> dict:
    d = {
        "id": r["id"], "kind": r["kind"], "url": r["url"], "title": r["title"],
        "note": r["note"], "summary": r["summary"], "tags": list(r["tags"] or []),
        "status": r["status"], "favorite": bool(r["favorite"]), "source": r["source"],
        "image": _thumb(r),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }
    if full:
        raw = r["raw_text"] or ""
        speech = ""
        m = re.search(r"Речь в видео(?: \(субтитры\))?: (.+)", raw, re.S)
        if m:
            speech = m.group(1).strip()
        d["speech"] = speech
    return d


def _stem(w: str) -> str:
    """Грубое «основание» русского слова: чтобы «субтитры» находило «субтитров»."""
    w = w.lower().replace("ё", "е")
    return w[:max(4, len(w) - 3)] if len(w) > 5 else w


def _snippet(r, q: str) -> tuple[str, list[str]]:
    """Фрагмент текста, где лучше всего видно, почему запись нашлась, и слова для подсветки."""
    stems = [_stem(w) for w in WORD.findall(q) if len(w) > 2]
    texts = [r["summary"] or "", r["note"] or "", r["raw_text"] or ""]
    best, best_score = "", 0
    for t in texts:
        for sent in re.split(r"(?<=[.!?…])\s+|\n", t):
            words = [_stem(w) for w in WORD.findall(sent)]
            score = sum(1 for s in stems if any(w.startswith(s) for w in words))
            if score > best_score and len(sent) > 15:
                best, best_score = sent.strip(), score
    if not best:
        best = (r["summary"] or r["note"] or "")[:220]
    if len(best) > 240:
        best = best[:237].rsplit(" ", 1)[0] + "…"
    hits = sorted({w for w in WORD.findall(best) if any(_stem(w).startswith(s) for s in stems)})
    return best, hits


# ---------- вход ----------

PUBLIC = {"/api/login/start", "/api/login/poll", "/api/login"}


@web.middleware
async def cors_and_auth(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    elif request.path.startswith("/api/") and request.path not in PUBLIC:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        uid = await db.user_by_token(token) if token else None
        if not uid:
            resp = web.json_response({"error": "Нужно войти через Telegram"}, status=401)
        else:
            request["uid"] = uid
            resp = await handler(request)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return resp


async def login_start(request: web.Request):
    key = secrets.token_urlsafe(18)
    await db.login_start(key)
    return web.json_response({"key": key, "bot": BOT_USERNAME,
                              "url": f"https://t.me/{BOT_USERNAME}?start=login_{key}"})


async def login_poll(request: web.Request):
    token = await db.login_poll(request.query.get("key", ""))
    return web.json_response({"token": token})


async def login_code(request: web.Request):
    """Запасной вход по коду из /login."""
    body = await request.json()
    code = str(body.get("code", "")).strip()
    uid = await db.use_login_code(code) if code else None
    if not uid:
        return web.json_response({"error": "Код не подошёл или устарел"}, status=400)
    token = secrets.token_urlsafe(32)
    await db.create_token(uid, token)
    return web.json_response({"token": token})


async def confirm_login(key: str, uid: int) -> bool:
    """Вызывается ботом, когда ты нажал «Подтвердить вход»."""
    token = secrets.token_urlsafe(32)
    if not await db.login_confirm(key, uid, token):
        return False
    await db.create_token(uid, token)
    return True


# ---------- данные ----------

async def me(request: web.Request):
    u = await db.get_user(request["uid"])
    return web.json_response({"id": u["id"], "username": u["username"], "name": u["name"],
                              "bot": BOT_USERNAME})


def _filters(request):
    q = request.query
    view = q.get("view", "all")
    f = {"tag": q.get("tag") or None, "kinds": KIND_GROUPS.get(q.get("kind", "")),
         "sort": q.get("sort", "new")}
    if view == "favorite":
        f["favorite"] = True
    elif view in ("new", "try", "used", "trash"):
        f["status"] = view
    return f


async def items(request: web.Request):
    uid = request["uid"]
    q = request.query.get("q", "").strip()
    f = _filters(request)
    offset = int(request.query.get("offset", 0))

    if not q:
        rows = await db.query_items(uid, **f, limit=60, offset=offset)
        return web.json_response({"items": [_item(r) for r in rows]})

    # поиск: сначала по смыслу, потом по словам
    qv = await ai.embed(q)
    ids = ai.rank(qv, await db.all_embeddings(uid), top=60, min_score=0.3)
    sem = await db.query_items(uid, ids=ids, **{**f, "sort": "new"}, limit=60, with_raw=True)
    words = await db.query_items(uid, text=q, **f, limit=40, with_raw=True)
    seen = {r["id"] for r in sem}
    out = []
    for r, how in [(r, "meaning") for r in sem] + [(r, "words") for r in words if r["id"] not in seen]:
        d = _item(r)
        d["match"], d["highlight"] = _snippet(r, q)
        d["match_kind"] = how
        out.append(d)
    return web.json_response({"items": out})


async def item(request: web.Request):
    r = await db.get_item(request["uid"], int(request.match_info["id"]))
    if not r:
        return web.json_response({"error": "Такой записи нет"}, status=404)
    return web.json_response(_item(r, full=True))


async def tags(request: web.Request):
    t = await db.user_tags(request["uid"])
    return web.json_response({"tags": [{"tag": n, "count": c} for n, c in t]})


async def stats(request: web.Request):
    s = await db.stats(request["uid"])
    s["import"] = await db.import_status(request["uid"])
    return web.json_response(s)


async def update(request: web.Request):
    body = await request.json()
    fields = {}
    if body.get("status") in ("new", "keep", "try", "used", "trash"):
        fields["status"] = body["status"]
    if isinstance(body.get("favorite"), bool):
        fields["favorite"] = body["favorite"]
    if isinstance(body.get("tags"), list):
        fields["tags"] = ai._clean_tags(body["tags"], limit=12) if body["tags"] else []
    if isinstance(body.get("title"), str) and body["title"].strip():
        fields["title"] = body["title"].strip()[:200]
    if isinstance(body.get("note"), str):
        fields["note"] = body["note"].strip()[:4000]
    ok = await db.update_item(request["uid"], int(request.match_info["id"]), **fields)
    return web.json_response({"ok": ok})


async def add(request: web.Request):
    """Быстрое сохранение из программы: ссылка или заметка."""
    uid = request["uid"]
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        return web.json_response({"error": "Буфер обмена пуст"}, status=400)
    urls = extract.find_urls(text)
    note = text
    for u in urls:
        note = note.replace(u, "")
    note = note.strip()
    saved, dups = [], []
    if not urls:
        saved.append(await core.process_item(uid, kind="note", note=note, text=note))
    for u in urls[:5]:
        dup = await db.find_by_url(uid, u)
        if dup and dup["status"] != "trash":
            dups.append(dup["id"])
            continue
        kind, title, body, image = await core.enrich_url(u)
        saved.append(await core.process_item(uid, kind=kind, url=u, title=title, note=note,
                                             text=body, image_url=image))
    return web.json_response({"saved": saved, "duplicates": dups})


# ---------- уборка тегов ----------

pending_tagmaps: dict[int, dict] = {}


async def tags_clean_preview(request: web.Request):
    uid = request["uid"]
    mapping = await core.suggest_tag_cleanup(uid)
    pending_tagmaps[uid] = mapping
    groups: dict[str, list[str]] = {}
    for old, new in mapping.items():
        groups.setdefault(new, []).append(old)
    return web.json_response({"groups": [{"to": k, "from": v} for k, v in groups.items()]})


async def tags_clean_apply(request: web.Request):
    mapping = pending_tagmaps.pop(request["uid"], None)
    if not mapping:
        return web.json_response({"error": "Сначала запроси предложение"}, status=400)
    n = await db.remap_tags(request["uid"], mapping)
    return web.json_response({"updated": n})


async def img(request: web.Request):
    data = await db.get_thumb(request.match_info["key"])
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def index(request: web.Request):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def _trash_janitor():
    while True:
        try:
            await db.purge_trash()
        except Exception as e:
            print("purge error:", e)
        await asyncio.sleep(6 * 3600)


async def start(bot):
    global BOT_USERNAME
    try:
        BOT_USERNAME = (await bot.get_me()).username or BOT_USERNAME
    except Exception:
        pass
    app = web.Application(middlewares=[cors_and_auth], client_max_size=2 * 1024 * 1024)
    app.add_routes([
        web.post("/api/login/start", login_start),
        web.get("/api/login/poll", login_poll),
        web.post("/api/login", login_code),
        web.get("/api/me", me),
        web.get("/api/items", items),
        web.get("/api/items/{id}", item),
        web.patch("/api/items/{id}", update),
        web.post("/api/items", add),
        web.get("/api/tags", tags),
        web.post("/api/tags/clean", tags_clean_preview),
        web.post("/api/tags/clean/apply", tags_clean_apply),
        web.get("/api/stats", stats),
        web.get("/img/{key}.jpg", img),
        web.get("/", index),
    ])
    if STATIC.exists():
        app.router.add_static("/static/", STATIC)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("WEB_PORT", 8080))  # домен Railway смотрит на порт 8080
    await web.TCPSite(runner, "0.0.0.0", port).start()
    asyncio.create_task(_trash_janitor())
    asyncio.create_task(core.backfill_loop())
    asyncio.create_task(core.thumbs_loop())
    print(f"Веб-интерфейс на порту {port}")
