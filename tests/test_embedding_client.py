import asyncio
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from embedding_client import EmbeddingWorker


class EmbeddingWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixtures = str(Path(__file__).parent / "fixtures")
        self.environment = patch.dict(os.environ, {"PYTHONPATH": fixtures})
        self.environment.start()
        self.worker = EmbeddingWorker("test-model", idle_seconds=0.1, timeout=3)

    async def asyncTearDown(self):
        await self.worker.aclose()
        self.environment.stop()

    async def test_reuses_one_child_for_concurrent_requests(self):
        first, second = await asyncio.gather(self.worker.embed("one"), self.worker.embed("two!"))
        self.assertEqual(first[0], 3)
        self.assertEqual(second[0], 4)
        self.assertEqual(first[1], second[1])
        self.assertNotEqual(first[1], os.getpid())
        self.assertEqual(len(first), 384)

    async def test_preserves_unicode_and_existing_text_limit(self):
        short = await self.worker.embed("Привет\nмир")
        long = await self.worker.embed("я" * 2500)
        self.assertEqual(short[0], 10)
        self.assertEqual(long[0], 2000)

    async def test_idle_child_exits_and_next_request_starts_a_new_one(self):
        first = await self.worker.embed("one")
        child = self.worker._process
        await asyncio.wait_for(child.wait(), 3)
        second = await self.worker.embed("two")
        self.assertNotEqual(first[1], second[1])

    async def test_active_request_is_not_killed_by_idle_timeout(self):
        first = await self.worker.embed("one")
        await asyncio.sleep(0.04)
        second = await self.worker.embed("slow")
        self.assertEqual(second[0], 4)
        self.assertEqual(first[1], second[1])

    async def test_timeout_reaps_child_and_next_request_recovers(self):
        await self.worker.embed("one")
        child = self.worker._process
        self.worker.timeout = 0.05
        with self.assertRaises(TimeoutError):
            await self.worker.embed("slow")
        self.assertIsNotNone(child.returncode)
        self.worker.timeout = 3
        next_vector = await self.worker.embed("next")
        self.assertNotEqual(next_vector[1], child.pid)

    async def test_cancellation_reaps_child_and_next_request_recovers(self):
        await self.worker.embed("one")
        child = self.worker._process
        pending = asyncio.create_task(self.worker.embed("slow"))
        await asyncio.sleep(0.05)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertIsNotNone(child.returncode)
        next_vector = await self.worker.embed("next")
        self.assertNotEqual(next_vector[1], child.pid)

    async def test_model_error_is_reported_and_next_request_recovers(self):
        with self.assertRaisesRegex(RuntimeError, "ValueError"):
            await self.worker.embed("error")
        self.assertEqual((await self.worker.embed("next"))[0], 4)

    async def test_unexpected_exit_is_reported_and_next_request_recovers(self):
        with self.assertRaises(RuntimeError):
            await self.worker.embed("crash")
        self.assertEqual((await self.worker.embed("next"))[0], 4)


if __name__ == "__main__":
    unittest.main()
