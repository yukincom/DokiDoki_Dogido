"""採取記録への認証情報混入と、古いビルドへの識別情報の取り違えを検査する。"""
import json
from pathlib import Path
import tempfile
import unittest

from launch_gguf_capture import fingerprint, recorded_options, verify_build


class LaunchBindingTests(unittest.TestCase):
    def test_credentials_and_unknown_options_do_not_enter_exported_identity(self):
        record = recorded_options([
            "-m", "/models/weights.gguf", "--api-key", "SECRET-ONE",
            "--api-key=SECRET-TWO", "-hft", "SECRET-THREE",
            "--hf-token=SECRET-FOUR", "--future-token", "SECRET-FIVE",
            "-np", "1", "--spec-type=draft-mtp",
        ])
        self.assertNotIn("SECRET", json.dumps(record))
        self.assertEqual(record["--spec-type"], ["draft-mtp"])
        self.assertEqual(record["-np"], ["1"])

    def test_stale_collector_binary_and_library_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            own, build = root / "source", root / "build"
            own.mkdir()
            (build / "bin").mkdir(parents=True)
            header, patch = own / "gguf_capture.h", own / "gguf_capture.patch"
            binary, library = build / "bin/llama-server", build / "bin/libllama.dylib"
            for path in (header, patch, binary, library):
                path.write_bytes(path.name.encode())
            receipt = {"collector": fingerprint(header), "source_patch": fingerprint(patch),
                       "server_binary": fingerprint(binary),
                       "libraries": {library.name: fingerprint(library)["sha256"]}}
            (build / "dogido-capture-build.json").write_text(json.dumps(receipt))
            self.assertEqual(verify_build(binary, own), receipt)
            for path in (header, patch, binary, library):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(ValueError, "binding mismatch"):
                    verify_build(binary, own)
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
