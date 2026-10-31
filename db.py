"""База данных: PostgreSQL. Каждая запись привязана к user_id — готово к нескольким пользователям."""
import asyncpg

from config import DATABASE_URL

pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT PRIMARY KEY,          -- Telegram ID
    username    TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS items (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL,               -- link / youtube / reel / note / voice / photo
    url         TEXT,
    title       TEXT,
    raw_text    TEXT,                        -- что удалось достать из источника
    note        TEXT,                        -- твоя пометка
    summary     TEXT,                        -- выжимка от ИИ
    tags        TEXT[] DEFAULT '{}',
    status      TEXT DEFAULT 'new',          -- new / try / used / trash
    source      TEXT,                        -- откуда переслано
    embedding   REAL[],                      -- вектор для поиска по смыслу
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS items_user_idx ON items(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS items_url_idx  ON items(user_id, url);

-- для импорта: id исходного сообщения, чтобы не импортировать одно и то же дважды
ALTER TABLE items ADD COLUMN IF NOT EXISTS ext_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS items_ext_idx ON items(user_id, ext_id) WHERE ext_id IS NOT NULL;

ALTER TABLE items ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS favorite BOOLEAN DEFAULT false;
ALTER TABLE items ADD COLUMN IF NOT EXISTS trashed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS thumb_key TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS thumb_tries INT DEFAULT 0;

-- обложки храним у себя: ссылки Instagram на картинки протухают через пару дней
CREATE TABLE IF NOT EXISTS thumbs (
    key         TEXT PRIMARY KEY,
    item_id     INT REFERENCES items(id) ON DELETE CASCADE,
    data        BYTEA NOT NULL
);

CREATE TABLE IF NOT EXISTS login_sessions (
    key         TEXT PRIMARY KEY,
    user_id     BIGINT,
    token       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- вход в программу/сайт: одноразовый код из бота → постоянный токен устройства
CREATE TABLE IF NOT EXISTS login_codes (
    code        TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token       TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now(),
    last_used   TIMESTAMPTZ DEFAULT now()
);

-- незаконченный импорт продолжится после перезапуска сервера
CREATE TABLE IF NOT EXISTS imports (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id),
    chat_id     BIGINT NOT NULL,
    entries     JSONB NOT NULL,
    done        INT DEFAULT 0
);
"""


async def init():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as con:
        await con.execute(SCHEMA)


async def ensure_user(user_id: int, username: str | None, name: str | None = None):
    await pool.execute(
        "INSERT INTO users(id, username, name) VALUES($1, $2, $3) "
        "ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username, "
        "name = coalesce(EXCLUDED.name, users.name)",
        user_id, username, name,
    )


async def find_by_url(user_id: int, url: str):
    return await pool.fetchrow(
        "SELECT * FROM items WHERE user_id=$1 AND url=$2 LIMIT 1", user_id, url
    )


async def add_item(user_id: int, **f) -> int:
    return await pool.fetchval(
        """INSERT INTO items(user_id, kind, url, title, raw_text, note, summary, tags, source,
                             embedding, ext_id, created_at, image_url)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, coalesce($12, now()), $13) RETURNING id""",
        user_id, f["kind"], f.get("url"), f.get("title"), f.get("raw_text"), f.get("note"),
        f.get("summary"), f.get("tags", []), f.get("source"), f.get("embedding"),
        f.get("ext_id"), f.get("created_at"), f.get("image_url"),
    )


# ---------- для программы/сайта ----------

ITEM_COLS = ("id, kind, url, title, note, summary, tags, status, favorite, source, image_url, "
             "thumb_key, created_at")


async def query_items(user_id: int, *, ids: list[int] | None = None, tag: str | None = None,
                      kinds: list[str] | None = None, status: str | None = None,
                      favorite: bool = False, text: str | None = None, sort: str = "new",
                      limit: int = 60, offset: int = 0, with_raw: bool = False):
    where, args = ["user_id=$1"], [user_id]

    def add(cond, val):
        args.append(val)
        where.append(cond.replace("?", f"${len(args)}"))

    if ids is not None:
        add("id = ANY(?)", ids)
    if tag:
        add("? = ANY(tags)", tag)
    if kinds:
        add("kind = ANY(?)", kinds)
    if favorite:
        where.append("favorite")
    if status:
        add("status = ?", status)
    else:
        where.append("status <> 'trash'")
    if text:
        add("(title ILIKE ? OR summary ILIKE ? OR note ILIKE ? OR raw_text ILIKE ? "
            "OR array_to_string(tags,' ') ILIKE ?)", f"%{text}%")
    order = "created_at ASC, id ASC" if sort == "old" else "created_at DESC, id DESC"
    cols = ITEM_COLS + (", raw_text" if with_raw else "")
    sql = (f"SELECT {cols} FROM items WHERE {' AND '.join(where)} ORDER BY {order} "
           f"LIMIT {int(limit)} OFFSET {int(offset)}")
    rows = await pool.fetch(sql, *args)
    if ids is not None:
        pos = {i: n for n, i in enumerate(ids)}
        rows = sorted(rows, key=lambda r: pos.get(r["id"], 10**9))
    return rows


async def get_item(user_id: int, item_id: int):
    return await pool.fetchrow(f"SELECT {ITEM_COLS}, raw_text FROM items WHERE user_id=$1 AND id=$2",
                               user_id, item_id)


async def update_item(user_id: int, item_id: int, **f) -> bool:
    sets, args = [], [user_id, item_id]
    for col in ("status", "tags", "title", "note", "favorite"):
        if col in f and f[col] is not None:
            args.append(f[col])
            sets.append(f"{col}=${len(args)}")
            if col == "status":
                sets.append(f"trashed_at = CASE WHEN ${len(args)} = 'trash' THEN now() END")
    if not sets:
        return False
    res = await pool.execute(f"UPDATE items SET {', '.join(sets)} WHERE user_id=$1 AND id=$2", *args)
    return res.endswith("1")


async def stats(user_id: int) -> dict:
    r = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE status <> 'trash') total, "
        "count(*) FILTER (WHERE favorite AND status <> 'trash') favorite, "
        "count(*) FILTER (WHERE status = 'new') new, "
        "count(*) FILTER (WHERE status = 'try') try, "
        "count(*) FILTER (WHERE status = 'used') used, "
        "count(*) FILTER (WHERE status = 'trash') trash, "
        "count(*) FILTER (WHERE status <> 'trash' AND kind = 'reel') reel, "
        "count(*) FILTER (WHERE status <> 'trash' AND kind = 'youtube') youtube, "
        "count(*) FILTER (WHERE status <> 'trash' AND kind = 'link') link, "
        "count(*) FILTER (WHERE status <> 'trash' AND kind IN ('note','voice')) note, "
        "count(*) FILTER (WHERE status <> 'trash' AND kind IN ('photo','video')) media "
        "FROM items WHERE user_id=$1", user_id)
    return dict(r)


async def purge_trash():
    """Удаляем из корзины то, что пролежало там больше 30 дней."""
    await pool.execute("DELETE FROM items WHERE status='trash' AND trashed_at < now() - interval '30 days'")


async def get_user(user_id: int):
    return await pool.fetchrow("SELECT id, username, name FROM users WHERE id=$1", user_id)


# ---------- вход через Telegram ----------

async def login_start(key: str):
    await pool.execute("DELETE FROM login_sessions WHERE created_at < now() - interval '15 minutes'")
    await pool.execute("INSERT INTO login_sessions(key) VALUES($1)", key)


async def login_confirm(key: str, user_id: int, token: str) -> bool:
    res = await pool.execute(
        "UPDATE login_sessions SET user_id=$2, token=$3 WHERE key=$1 AND user_id IS NULL "
        "AND created_at > now() - interval '15 minutes'", key, user_id, token)
    return res.endswith("1")


async def login_poll(key: str) -> str | None:
    return await pool.fetchval(
        "DELETE FROM login_sessions WHERE key=$1 AND token IS NOT NULL RETURNING token", key)


async def import_status(user_id: int):
    r = await pool.fetchrow("SELECT jsonb_array_length(entries) total, done FROM imports "
                            "WHERE user_id=$1", user_id)
    return dict(r) if r else None


async def save_login_code(user_id: int, code: str):
    await pool.execute("DELETE FROM login_codes WHERE user_id=$1 OR expires_at < now()", user_id)
    await pool.execute("INSERT INTO login_codes(code, user_id, expires_at) "
                       "VALUES($1, $2, now() + interval '10 minutes')", code, user_id)


async def use_login_code(code: str) -> int | None:
    return await pool.fetchval("DELETE FROM login_codes WHERE code=$1 AND expires_at > now() "
                               "RETURNING user_id", code)


async def create_token(user_id: int, token: str):
    await pool.execute("INSERT INTO tokens(token, user_id) VALUES($1, $2)", token, user_id)


async def user_by_token(token: str) -> int | None:
    return await pool.fetchval("UPDATE tokens SET last_used=now() WHERE token=$1 RETURNING user_id",
                               token)


# ---------- незаконченные импорты ----------

async def import_save(user_id: int, chat_id: int, entries_json: str):
    await pool.execute(
        "INSERT INTO imports(user_id, chat_id, entries, done) VALUES($1,$2,$3::jsonb,0) "
        "ON CONFLICT (user_id) DO UPDATE SET chat_id=$2, entries=$3::jsonb, done=0",
        user_id, chat_id, entries_json)


async def import_progress(user_id: int, done: int):
    await pool.execute("UPDATE imports SET done=$2 WHERE user_id=$1", user_id, done)


async def import_finish(user_id: int):
    await pool.execute("DELETE FROM imports WHERE user_id=$1", user_id)


async def imports_pending():
    return await pool.fetch("SELECT user_id, chat_id, entries::text AS entries, done FROM imports")


async def ext_exists(user_id: int, ext_id: str) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM items WHERE user_id=$1 AND ext_id=$2)", user_id, ext_id
    )


async def remap_tags(user_id: int, mapping: dict[str, str]) -> int:
    """Переименовать/объединить теги по словарю {старый: новый}. Возвращает число изменённых записей."""
    rows = await pool.fetch("SELECT id, tags FROM items WHERE user_id=$1", user_id)
    updates = []
    for r in rows:
        old = list(r["tags"] or [])
        new = []
        for t in old:
            t2 = mapping.get(t, t)
            if t2 and t2 not in new:
                new.append(t2)
        if new != old:
            updates.append((new, r["id"]))
    if updates:
        await pool.executemany("UPDATE items SET tags=$1 WHERE id=$2", updates)
    return len(updates)


async def user_tags(user_id: int) -> list[tuple[str, int]]:
    rows = await pool.fetch(
        "SELECT t, count(*) c FROM items, unnest(tags) t "
        "WHERE user_id=$1 AND status <> 'trash' GROUP BY t ORDER BY c DESC",
        user_id,
    )
    return [(r["t"], r["c"]) for r in rows]


async def all_embeddings(user_id: int):
    return await pool.fetch(
        "SELECT id, embedding FROM items "
        "WHERE user_id=$1 AND embedding IS NOT NULL AND status <> 'trash'",
        user_id,
    )


async def get_items(user_id: int, ids: list[int]):
    rows = await pool.fetch("SELECT * FROM items WHERE user_id=$1 AND id = ANY($2)", user_id, ids)
    order = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r["id"]])


async def last_items(user_id: int, limit: int = 10):
    return await pool.fetch(
        "SELECT * FROM items WHERE user_id=$1 AND status <> 'trash' "
        "ORDER BY id DESC LIMIT $2",
        user_id, limit,
    )


async def by_tag(user_id: int, tag: str, limit: int = 15):
    return await pool.fetch(
        "SELECT * FROM items WHERE user_id=$1 AND $2 = ANY(tags) AND status <> 'trash' "
        "ORDER BY created_at DESC LIMIT $3",
        user_id, tag, limit,
    )


async def set_status(user_id: int, item_id: int, status: str) -> bool:
    res = await pool.execute(
        "UPDATE items SET status=$3, trashed_at = CASE WHEN $3 = 'trash' THEN now() END "
        "WHERE user_id=$1 AND id=$2", user_id, item_id, status
    )
    return res.endswith("1")


async def count(user_id: int) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM items WHERE user_id=$1 AND status <> 'trash'", user_id
    )


async def save_thumb(item_id: int, key: str, data: bytes):
    await pool.execute("INSERT INTO thumbs(key, item_id, data) VALUES($1,$2,$3) ON CONFLICT (key) DO NOTHING",
                       key, item_id, data)
    await pool.execute("UPDATE items SET thumb_key=$2 WHERE id=$1", item_id, key)


async def thumb_failed(item_id: int):
    await pool.execute("UPDATE items SET thumb_tries = coalesce(thumb_tries, 0) + 1 WHERE id=$1", item_id)


async def get_thumb(key: str) -> bytes | None:
    return await pool.fetchval("SELECT data FROM thumbs WHERE key=$1", key)


async def items_without_thumbs(limit: int = 10):
    return await pool.fetch(
        "SELECT id, kind, url, image_url FROM items WHERE thumb_key IS NULL AND url IS NOT NULL "
        "AND coalesce(thumb_tries, 0) < 2 AND status <> 'trash' AND kind NOT IN ('note','voice') "
        "ORDER BY id DESC LIMIT $1", limit)


async def empty_trash(user_id: int, item_id: int | None = None) -> int:
    """Удаляет навсегда всё из корзины (или одну запись из корзины)."""
    if item_id:
        res = await pool.execute("DELETE FROM items WHERE user_id=$1 AND id=$2 AND status='trash'", user_id, item_id)
    else:
        res = await pool.execute("DELETE FROM items WHERE user_id=$1 AND status='trash'", user_id)
    return int(res.split()[-1])
