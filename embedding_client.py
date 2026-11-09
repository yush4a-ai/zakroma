"""Keep the search model out of the long-lived bot process."""
import asyncio
import json
import math
import os
from pathlib import Path
import sys


class EmbeddingWorker:
    def __init__(self, model_name: str, *, idle_seconds: float = 30, timeout: float = 120):
        self.model_name = model_name
        self.idle_seconds = idle_seconds
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._process = None
        self._idle_handle = None
        self._generation = 0

    async def embed(self, text: str) -> list[float]:
        # One model and one inference at a time, including during bulk imports.
        async with self._lock:
            self._generation += 1
            self._cancel_idle()
            try:
                async with asyncio.timeout(self.timeout):
                    process = await self._get_process()
                    process.stdin.write((json.dumps(text[:2000], ensure_ascii=False) + "\n").encode())
                    await process.stdin.drain()
                    line = await process.stdout.readline()
                    if not line:
                        raise RuntimeError("Search model worker exited without a result")
                    result = json.loads(line)
                    if isinstance(result, dict) and "error" in result:
                        raise RuntimeError(f"Search model worker failed: {result['error']}")
                    if (not isinstance(result, list) or len(result) != 384
                            or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in result)):
                        raise RuntimeError("Search model worker returned an invalid vector")
                    return [float(x) for x in result]
            except BaseException:
                # A cancelled/failed request must not leave a response for the next caller.
                await self._stop()
                raise
            finally:
                if self._process is not None:
                    generation = self._generation
                    self._idle_handle = asyncio.get_running_loop().call_later(
                        self.idle_seconds,
                        lambda: asyncio.create_task(self._expire(generation)),
                    )

    async def _get_process(self):
        if self._process is None or self._process.returncode is not None:
            env = os.environ.copy()
            env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
                       HF_HUB_DISABLE_TELEMETRY="1")
            self._process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("embedding_worker.py")), self.model_name,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=env,
            )
        return self._process

    def _cancel_idle(self):
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    async def _stop(self):
        self._cancel_idle()
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def _expire(self, generation):
        async with self._lock:
            # A timer already queued behind a request must not stop a freshly reused model.
            if self._generation == generation:
                await self._stop()

    async def aclose(self):
        async with self._lock:
            self._generation += 1
            await self._stop()
