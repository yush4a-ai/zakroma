import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from embedding_worker import release_file_cache


class FileCacheTests(unittest.TestCase):
    def test_releasing_cache_preserves_downloaded_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            content = b"cached model bytes" * 100
            path.write_bytes(content)
            release_file_cache(directory)
            self.assertEqual(path.read_bytes(), content)

    def test_unsupported_advice_does_not_prevent_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            path.write_bytes(b"model")
            with (patch.object(os, "posix_fadvise", side_effect=OSError("unsupported"), create=True),
                  patch.object(os, "POSIX_FADV_DONTNEED", 4, create=True)):
                release_file_cache(directory)
            self.assertEqual(path.read_bytes(), b"model")

    def test_platform_without_advice_is_supported(self):
        with patch.object(os, "posix_fadvise", None, create=True):
            release_file_cache("unused-cache-directory")


if __name__ == "__main__":
    unittest.main()
