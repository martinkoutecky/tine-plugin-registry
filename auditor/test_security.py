import pathlib
import tempfile
import unittest

from codex_review import source_bundle
from validation import validate_source_tree, validate_submission


class AuditorSecurityTests(unittest.TestCase):
    def test_submission_paths_and_identity_are_strict(self):
        good = {
            "schemaVersion": 1,
            "pluginId": "page.tine.example",
            "version": "0.1.0",
            "repository": "https://github.com/example/tine-plugin-example",
            "commit": "a" * 40,
            "manifestPath": "plugin/manifest.json",
        }
        self.assertEqual(validate_submission(good), good)
        with self.assertRaises(ValueError):
            validate_submission({**good, "pluginId": "../../outside"})
        with self.assertRaises(ValueError):
            validate_submission({**good, "manifestPath": "../manifest.json"})
        with self.assertRaises(ValueError):
            validate_submission({**good, "repository": "https://github.com/example/repo?upload=1"})

    def test_source_tree_rejects_symlinks_before_host_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            secret = root.parent / "auditor-test-secret"
            secret.write_text("must-not-be-read")
            try:
                (root / "source.rs").symlink_to(secret)
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    validate_source_tree(root)
                bundle, _ = source_bundle([("plugin", root)], 10_000)
                self.assertNotIn("must-not-be-read", bundle)
            finally:
                secret.unlink(missing_ok=True)

    def test_truncated_source_forces_an_explicit_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "large.rs").write_text("x" * 1_000)
            bundle, truncated = source_bundle([("plugin", root)], 100)
            self.assertTrue(truncated)
            self.assertIn("automatic quarantine", bundle)


if __name__ == "__main__":
    unittest.main()
