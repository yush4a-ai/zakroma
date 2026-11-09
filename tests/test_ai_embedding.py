import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest


class AiEmbeddingIntegrationTests(unittest.TestCase):
    def test_embedding_does_not_load_model_in_bot_process(self):
        env = os.environ.copy()
        env.update(BOT_TOKEN="test", DATABASE_URL="postgresql://test",
                   GROQ_API_KEY="test", PYTHONPATH=str(Path(__file__).parent / "fixtures"))
        result = subprocess.run(
            [sys.executable, "-c", """
import asyncio, os, sys
import ai
async def check():
    vector = await ai.embed('привет')
    assert vector[0] == 6, vector
    assert vector[1] != os.getpid(), 'model ran inside the bot process'
    assert 'fastembed' not in sys.modules, 'heavy model library remains in bot'
    await ai._embedding_worker.aclose()
asyncio.run(check())
"""], env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
