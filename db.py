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
"""


async def init():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as con:
        await con.execute(SCHEMA)


async def ensure_user(user_id: int, username: str | None):
    await pool.execute(
        "INSERT INTO users(id, username) VALUES($1, $2) "
        "ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username",
        user_id, username,
    )


async def find_by_url(user_id: int, url: str):
    return await pool.fetchrow(
        "SELECT * FROM items WHERE user_id=$1 AND url=$2 LIMIT 1", user_id, url
    )


async def add_item(user_id: int, **f) -> int:
    return await pool.fetchval(
        """INSERT INTO items(user_id, kind, url, title, raw_text, note, summary, tags, source,
                             embedding, ext_id, created_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, coalesce($12, now())) RETURNING id""",
        user_id, f["kind"], f.get("url"), f.get("title"), f.get("raw_text"), f.get("note"),
        f.get("summary"), f.get("tags", []), f.get("source"), f.get("embedding"),
        f.get("ext_id"), f.get("created_at"),
    )


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
        "UPDATE items SET status=$3 WHERE user_id=$1 AND id=$2", user_id, item_id, status
    )
    return res.endswith("1")


async def count(user_id: int) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM items WHERE user_id=$1 AND status <> 'trash'", user_id
    )
