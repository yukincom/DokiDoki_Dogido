"""Protect stable application identity across manager updates."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("build_app", Path(__file__).resolve().parents[1] / "Tools/build_app.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SigningTests(unittest.TestCase):
    first = "1" * 40
    second = "2" * 40

    def test_named_identity_resolves_exactly_and_deduplicates_policy_sections(self):
        rows = f'  1) {self.first} "{builder.LOCAL_SIGNING_IDENTITY}"\n'
        rows += f'  2) {self.second} "Another App"\n'
        self.assertEqual(builder.resolve_signing_identity(builder.LOCAL_SIGNING_IDENTITY, rows * 2), self.first)

    def test_ambiguous_or_missing_identity_does_not_fall_back_to_ad_hoc(self):
        rows = f' 1) {self.first} "{builder.LOCAL_SIGNING_IDENTITY}"\n'
        rows += f' 2) {self.second} "{builder.LOCAL_SIGNING_IDENTITY}"\n'
        for output in ("0 identities found", rows):
            with self.subTest(output=output), self.assertRaises(SystemExit):
                builder.resolve_signing_identity(builder.LOCAL_SIGNING_IDENTITY, output)

    def test_fingerprint_can_disambiguate_a_repeated_name(self):
        fingerprint = "ABCDEF1234" * 4
        rows = f' 1) {fingerprint} "Repeated Name"\n 2) {self.second} "Repeated Name"\n'
        self.assertEqual(builder.resolve_signing_identity(fingerprint.lower(), rows), fingerprint)

    def test_ad_hoc_is_explicit_and_does_not_access_the_keychain(self):
        with patch.object(builder.subprocess, "run") as run:
            self.assertEqual(builder.signing_key(builder.LOCAL_SIGNING_IDENTITY, True), "-")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
