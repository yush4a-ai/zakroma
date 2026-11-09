"""Manual Linux check using the real, cached search model (no API requests)."""
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embedding_client import EmbeddingWorker

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TEXTS = ["Как настроить микрофон и убрать шум?", "Useful video editing tools",
         ("Сохранённая ссылка про монтаж видео, звук и обработку фотографий. " * 50)[:2000]]


def rss_kb(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])


async def main():
    baseline_code = """
import json, sys
from fastembed import TextEmbedding
model = TextEmbedding(sys.argv[1], local_files_only=True)
print(json.dumps([[float(x) for x in next(iter(model.embed([text[:2000]])))]
                  for text in json.loads(sys.stdin.read())]))
"""
    baseline = subprocess.run([sys.executable, "-c", baseline_code, MODEL],
                              input=json.dumps(TEXTS), capture_output=True, text=True,
                              check=True, timeout=90)
    expected = json.loads(baseline.stdout)
    worker = EmbeddingWorker(MODEL, idle_seconds=1)
    timings = []
    try:
        actual = []
        for text in TEXTS:
            start = time.monotonic()
            actual.append(await worker.embed(text))
            timings.append(round(time.monotonic() - start, 3))
        max_diff = max(abs(a - b) for av, bv in zip(actual, expected) for a, b in zip(av, bv))
        assert all(len(v) == 384 for v in actual)
        assert all(math.isclose(a, b, abs_tol=1e-5, rel_tol=1e-5)
                   for av, bv in zip(actual, expected) for a, b in zip(av, bv)), max_diff
        child = worker._process
        child_rss = rss_kb(child.pid)
        await asyncio.wait_for(child.wait(), 10)
        assert not Path(f"/proc/{child.pid}").exists(), "idle model still running"
        assert "fastembed" not in sys.modules, "model loaded into parent"
        print(json.dumps({"vectors_match": True, "max_vector_difference": max_diff,
                          "seconds_per_request": timings, "model_worker_rss_kb": child_rss,
                          "idle_worker_exited": True, "parent_rss_kb": rss_kb("self")}))
    finally:
        await worker.aclose()


if __name__ == "__main__":
    asyncio.run(main())
