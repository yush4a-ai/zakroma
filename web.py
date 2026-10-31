"""API и веб-интерфейс Закромов. Работает в том же процессе, что и бот."""
import json
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
KIND_GROUPS = {
    "reel": ["reel"],
    "youtube": ["youtube"],
    "link": ["link"],
    "note": ["note", "voice"],
    "media": ["photo", "video"],
}
YT_ID = re.compile(r"(?:v=|shorts/|youtu\.be/|embed/)([\w-]{11})")


def _thumb(r) -> str | None:
    if r["image_url"]:
        return r["image_url"]
    if r["url"]:
        m = YT_ID.search(r["url"])
        if m and ("youtu" in r["url"]):
            return f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
    return None


def _item(r) -> dict:
    return {
        "id": r["id"], "kind": r["kind"], "url": r["url"], "title": r["title"],
        "note": r["note"], "summary": r["summary"], "tags": list(r["tags"] or []),
        "status": r["status"], "source": r["source"], "image": _thumb(r),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


# ---------- вход ----------

@web.middleware
async def cors_and_auth(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    elif request.path.startswith("/api/") and request.path != "/api/login":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        uid = await db.user_by_token(token) if token else None
        if not uid:
            resp = web.json_response({"error": "Нужно войти: получи код в боте командой /login"},
                                     status=401)
        else:
            request["uid"] = uid
            resp = await handler(request)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return resp


async def login(request: web.Request):
    body = await request.json()
    code = str(body.get("code", "")).strip()
    uid = await db.use_login_code(code) if code else None
    if not uid:
        return web.json_response({"error": "Код не подошёл или устарел. Запроси новый в боте: /login"},
                                 status=400)
    token = secrets.token_urlsafe(32)
    await db.create_token(uid, token)
    return web.json_response({"token": token})


# ---------- данные ----------

async def items(request: web.Request):
    uid = request["uid"]
    q = request.query.get("q", "").strip()
    tag = request.query.get("tag") or None
    kinds = KIND_GROUPS.get(request.query.get("kind", ""))
    status = request.query.get("status") or None
    offset = int(request.query.get("offset", 0))

    if q:
        # поиск по смыслу + обычный поиск по словам, смысловые совпадения первыми
        qv = await ai.embed(q)
        ids = ai.rank(qv, await db.all_embeddings(uid), top=80, min_score=0.22)
        sem = await db.query_items(uid, ids=ids, tag=tag, kinds=kinds, status=status, limit=80)
        words = await db.query_items(uid, text=q, tag=tag, kinds=kinds, status=status, limit=40)
        seen = {r["id"] for r in sem}
        rows = list(sem) + [r for r in words if r["id"] not in seen]
    else:
        rows = await db.query_items(uid, tag=tag, kinds=kinds, status=status, limit=60, offset=offset)
    return web.json_response({"items": [_item(r) for r in rows]})


async def tags(request: web.Request):
    t = await db.user_tags(request["uid"])
    return web.json_response({"tags": [{"tag": n, "count": c} for n, c in t]})


async def stats(request: web.Request):
    return web.json_response(await db.stats(request["uid"]))


async def update(request: web.Request):
    body = await request.json()
    fields = {}
    if body.get("status") in ("new", "keep", "try", "used", "trash"):
        fields["status"] = body["status"]
    if isinstance(body.get("tags"), list):
        fields["tags"] = ai._clean_tags(body["tags"]) if body["tags"] else []
    if isinstance(body.get("title"), str) and body["title"].strip():
        fields["title"] = body["title"].strip()[:200]
    ok = await db.update_item(request["uid"], int(request.match_info["id"]), **fields)
    return web.json_response({"ok": ok})


async def add(request: web.Request):
    """Быстрое сохранение из программы: ссылка или заметка."""
    uid = request["uid"]
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        return web.json_response({"error": "Пусто"}, status=400)
    urls = extract.find_urls(text)
    note = text
    for u in urls:
        note = note.replace(u, "")
    note = note.strip()
    saved = []
    if not urls:
        saved.append(await core.process_item(uid, kind="note", note=note, text=note))
    for u in urls[:5]:
        dup = await db.find_by_url(uid, u)
        if dup and dup["status"] != "trash":
            continue
        kind, title, body, image = await core.enrich_url(u)
        saved.append(await core.process_item(uid, kind=kind, url=u, title=title, note=note,
                                             text=body, image_url=image))
    return web.json_response({"saved": [s["id"] for s in saved]})


async def index(request: web.Request):
    return web.FileResponse(STATIC / "index.html")


async def start(bot):
    app = web.Application(middlewares=[cors_and_auth], client_max_size=2 * 1024 * 1024)
    app.add_routes([
        web.post("/api/login", login),
        web.get("/api/items", items),
        web.get("/api/tags", tags),
        web.get("/api/stats", stats),
        web.patch("/api/items/{id}", update),
        web.post("/api/items", add),
        web.get("/", index),
    ])
    if STATIC.exists():
        app.router.add_static("/static/", STATIC)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Веб-интерфейс на порту {port}")
