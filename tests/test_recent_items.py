"""Recent saves against PostgreSQL, isolated in a rolled-back test schema.

Run with TEST_DATABASE_URL set to exercise the real queries and HTTP API.
"""
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
from uuid import uuid4

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer

try:
    import asyncpg
except ImportError:
    asyncpg = None


ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(asyncpg and TEST_DATABASE_URL, "Requires asyncpg and TEST_DATABASE_URL")
class RecentItemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = ModuleType("config")
        config.DATABASE_URL = TEST_DATABASE_URL
        spec = importlib.util.spec_from_file_location("recent_test_db", ROOT / "db.py")
        self.db = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"config": config}):
            spec.loader.exec_module(self.db)

        # These AI modules are not used by the listing/update endpoints.
        modules = {name: ModuleType(name) for name in ("ai", "core", "extract")}
        modules["db"] = self.db
        spec = importlib.util.spec_from_file_location("recent_test_web", ROOT / "web.py")
        self.web = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.web)

        self.conn = await asyncpg.connect(TEST_DATABASE_URL)
        self.transaction = self.conn.transaction()
        await self.transaction.start()
        self.addAsyncCleanup(self.conn.close)
        async def rollback():
            await self.transaction.rollback()
        self.addAsyncCleanup(rollback)
        schema = "zakroma_recent_test_" + uuid4().hex
        await self.conn.execute(f'CREATE SCHEMA "{schema}"')
        await self.conn.execute(f'SET LOCAL search_path TO "{schema}", pg_catalog')
        self.db.pool = self.conn
        await self.conn.execute(self.db.SCHEMA)
        # Simulate a migrated database for API tests; migration is tested separately.
        await self.conn.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS added_at TIMESTAMPTZ DEFAULT now()")
        await self.conn.execute("INSERT INTO users(id, username) VALUES (101, 'owner'), (202, 'other')")
        await self.db.create_token(101, "recent-test-token")

        app = aiohttp_web.Application(middlewares=[self.web.cors_and_auth])
        app.router.add_get("/api/items", self.web.items)
        app.router.add_get("/api/stats", self.web.stats)
        app.router.add_patch("/api/items/{id}", self.web.update)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def seed(self):
        await self.conn.execute("""
            INSERT INTO items(id, user_id, kind, title, created_at, added_at, status, favorite)
            VALUES
              (11,101,'reel','Old video saved today', '2020-01-01', now(), 'new', false),
              (12,101,'note','Used favorite', now(), now()-interval '1 day', 'used', true),
              (13,101,'link','Exactly seven days', now(), now()-interval '7 days', 'try', false),
              (14,101,'reel','Expired save', now(), now()-interval '7 days 1 microsecond', 'new', false),
              (15,101,'reel','In trash', now(), now(), 'trash', false),
              (16,202,'reel','Other user', now(), now(), 'new', false)
        """)

    async def get(self, path):
        response = await self.client.get(path, headers={"Authorization": "Bearer recent-test-token"})
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_recent_uses_save_time_with_a_seven_day_boundary(self):
        await self.seed()
        data = await self.get("/api/items?view=recent")
        self.assertEqual([r["id"] for r in data["items"]], [11, 12, 13])
        self.assertTrue(data["items"][0]["created_at"].startswith("2020-01-01"))
        self.assertNotEqual(data["items"][0]["added_at"], data["items"][0]["created_at"])

    async def test_recent_filters_and_counter_share_the_same_membership(self):
        await self.seed()
        data = await self.get("/api/items?view=recent&kind=reel&sort=old")
        self.assertEqual([r["id"] for r in data["items"]], [11])
        stats = await self.get("/api/stats")
        self.assertEqual(stats["recent"], 3)
        self.assertEqual(stats["total"], 4)

    async def test_status_and_favorite_do_not_reset_recent_but_trash_removes_it(self):
        await self.seed()
        response = await self.client.patch("/api/items/11", json={"status": "used", "favorite": True},
                                           headers={"Authorization": "Bearer recent-test-token"})
        self.assertEqual(response.status, 200)
        data = await self.get("/api/items?view=recent")
        self.assertEqual([r["id"] for r in data["items"]], [11, 12, 13])
        response = await self.client.patch("/api/items/11", json={"status": "trash"},
                                           headers={"Authorization": "Bearer recent-test-token"})
        self.assertEqual(response.status, 200)
        data = await self.get("/api/items?view=recent")
        self.assertEqual([r["id"] for r in data["items"]], [12, 13])
        self.assertEqual((await self.get("/api/stats"))["recent"], 2)
        self.assertIn(14, [r["id"] for r in (await self.get("/api/items"))["items"]])

    async def test_migration_preserves_legacy_dates_and_new_imports_get_save_time(self):
        await self.conn.execute("ALTER TABLE items DROP COLUMN added_at")
        legacy = await self.db.add_item(101, kind="note", title="Legacy")
        await self.conn.execute("UPDATE items SET created_at=now()-interval '20 days' WHERE id=$1", legacy)
        await self.conn.execute(self.db.SCHEMA)
        columns = await self.conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='items'")
        self.assertIn("added_at", [r["column_name"] for r in columns])
        old = await self.conn.fetchrow("SELECT created_at,added_at FROM items WHERE id=$1", legacy)
        self.assertEqual(old["added_at"], old["created_at"])
        source_date = await self.conn.fetchval("SELECT '2020-01-01'::timestamptz")
        imported = await self.db.add_item(101, kind="reel", title="Imported today", created_at=source_date)
        saved_at = await self.conn.fetchval("SELECT added_at FROM items WHERE id=$1", imported)
        self.assertEqual(saved_at, await self.conn.fetchval("SELECT now()"))
        await self.conn.execute(self.db.SCHEMA)
        self.assertEqual(await self.conn.fetchval("SELECT added_at FROM items WHERE id=$1", legacy), old["added_at"])
        self.assertEqual(await self.conn.fetchval("SELECT added_at FROM items WHERE id=$1", imported), saved_at)


if __name__ == "__main__":
    unittest.main()
