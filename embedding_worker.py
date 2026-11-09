"""JSON-lines worker: its exit releases the model and native inference buffers."""
from contextlib import redirect_stdout
import json
import sys


def main():
    # Reserve stdout for the protocol, including while the library loads/downloads.
    with redirect_stdout(sys.stderr):
        from fastembed import TextEmbedding
        model = TextEmbedding(sys.argv[1], threads=1)

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
