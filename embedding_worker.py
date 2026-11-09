"""JSON-lines worker: its exit releases the model and native inference buffers."""
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys


def release_file_cache(cache_dir):
    """Release clean model file pages from RAM, keeping the download on disk."""
    advise = getattr(os, "posix_fadvise", None)
    if cache_dir is None or advise is None:
        return
    try:
        for path in Path(cache_dir).rglob("*"):
            if path.is_file():
                try:
                    with path.open("rb") as file:
                        advise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except OSError:
                    pass  # Optional optimization; inference must still work.
    except OSError:
        pass


def main():
    # Reserve stdout for the protocol, including while the library loads/downloads.
    with redirect_stdout(sys.stderr):
        from fastembed import TextEmbedding
        model = TextEmbedding(sys.argv[1], threads=1)
    # ONNX/tokenizer data is now in the model's own memory. Railway also bills
    # the duplicate clean file cache, so let Linux reclaim those pages.
    release_file_cache(getattr(getattr(model, "model", None), "_model_dir", None))

    for line in sys.stdin.buffer:
        try:
            text = json.loads(line)
            with redirect_stdout(sys.stderr):
                vector = next(iter(model.embed([text[:2000]])))
                result = [float(x) for x in vector]
        except Exception as exc:
            result = {"error": type(exc).__name__}
        print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
