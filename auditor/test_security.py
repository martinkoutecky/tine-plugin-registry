import pathlib
import tempfile
import unittest

from codex_review import source_bundle
from audit import (
    defines_custom_property, narrow_theme_port_evidence, port_evidence_path_relevant, theme_custom_properties,
)
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
        theme = {
            "schemaVersion": 2,
            "kind": "theme",
            "packageId": "org.tine.theme.example",
            "version": "1.0.0",
            "repository": "https://github.com/example/tine-theme-example",
            "commit": "b" * 40,
            "manifestPath": "theme.json",
        }
        self.assertEqual(validate_submission(theme), theme)
        with self.assertRaises(ValueError):
            validate_submission({**theme, "manifestPath": "manifest.json"})

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

    def test_theme_port_evidence_excludes_application_code_and_generated_bundles(self):
        self.assertTrue(port_evidence_path_relevant(pathlib.Path("theme-config.css"), "theme"))
        self.assertTrue(port_evidence_path_relevant(pathlib.Path("README.md"), "theme"))
        self.assertFalse(port_evidence_path_relevant(pathlib.Path("app.bundle.css"), "theme"))
        self.assertFalse(port_evidence_path_relevant(pathlib.Path("app/graph-view.js"), "theme"))
        self.assertTrue(port_evidence_path_relevant(pathlib.Path("app/graph-view.js"), "plugin"))

    def test_large_theme_upstream_narrows_to_files_defining_the_themes_properties(self):
        manifest = {"modes": {"dark": {"--ls-primary-background-color": "#002b36"}, "light": {"--ls-link-text-color": "#106ba3"}}}
        names = theme_custom_properties(manifest)
        self.assertEqual(names, {"--ls-primary-background-color", "--ls-link-text-color"})
        self.assertTrue(defines_custom_property(":root { --ls-link-text-color : #000; }", names))
        self.assertFalse(defines_custom_property("a { color: var(--ls-link-text-color); }", names))
        blobs = {
            "b1": ".dark { --ls-primary-background-color: #002b36; }",
            "b2": "a { color: var(--ls-link-text-color); }",
            "b3": "AGPL text",
            "b4": "nested readme",
        }
        selected = [("src/vars.css", "b1"), ("src/block.css", "b2"), ("LICENSE.md", "b3"), ("deps/x/README.md", "b4")]
        fetched = []
        narrowed = narrow_theme_port_evidence(selected, names, blobs.__getitem__, fetched.extend)
        self.assertEqual([rel for rel, _ in narrowed], ["src/vars.css", "LICENSE.md"])
        self.assertIsNone(narrow_theme_port_evidence(selected[1:], names, blobs.__getitem__, fetched.extend))
        self.assertIsNone(narrow_theme_port_evidence(selected, set(), blobs.__getitem__, fetched.extend))


if __name__ == "__main__":
    unittest.main()
