"""Small stand-in for the external model; process lifecycle stays real."""
import os
import time


class TextEmbedding:
    def __init__(self, model_name, **kwargs):
        pass

    def embed(self, texts):
        for text in texts:
            if text == "slow":
                time.sleep(0.4)
            if text == "crash":
                os._exit(3)
            if text == "error":
                raise ValueError("model unavailable")
            print("model progress on stdout")
            yield [float(len(text)), float(os.getpid())] + [0.0] * 382
