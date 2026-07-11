import base64
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from publisher import canonical, publish


def envelope(plugin_id="page.tine.example"):
    manifest = {
        "schemaVersion": 1,
        "id": plugin_id,
        "name": "Example",
        "version": "0.1.0",
        "apiVersion": "0.1",
        "description": "Example plugin",
        "author": "Example",
        "license": "MIT",
        "source": "https://github.com/example/plugin",
        "entry": "plugin.wasm",
        "platforms": ["desktop"],
        "capabilities": [],
    }
    wasm = b"\0asm\x01\0\0\0"
    return {
        "format": "tine-plugin-audit-result/v1",
        "disposition": "publish",
        "submission": {
            "schemaVersion": 1,
            "pluginId": plugin_id,
            "version": "0.1.0",
            "repository": "https://github.com/example/plugin",
            "commit": "a" * 40,
            "manifestPath": "manifest.json",
        },
        "checker": {"status": "passed", "risk": "low"},
        "aiReview": {"disposition": "pass", "uncertain": False},
        "package": {
            "manifest": manifest,
            "manifestSha256": hashlib.sha256(canonical(manifest)).hexdigest(),
            "wasmBase64": base64.b64encode(wasm).decode(),
            "sha256": hashlib.sha256(wasm).hexdigest(),
        },
    }


class PublisherSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "registry"
        self.repo.mkdir()
        (self.repo / "index.json").write_text(
            json.dumps({"schemaVersion": 1, "generatedAt": "initial", "plugins": [], "revocations": []}) + "\n"
        )
        self.key = self.root / "key.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.key)], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_envelope(self, value):
        path = self.root / "envelope.json"
        path.write_text(json.dumps(value))
        return path

    def test_publisher_recomputes_and_pins_both_artifact_digests(self):
        result = publish(self.write_envelope(envelope()), self.repo, self.key)
        self.assertEqual(result[0], "published")
        index = json.loads((self.repo / "index.json").read_text())
        version = index["plugins"][0]["versions"][0]
        self.assertRegex(version["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(version["manifestSha256"], r"^[0-9a-f]{64}$")

    def test_publisher_rejects_traversal_and_tampered_bytes_before_writing(self):
        bad_path = envelope("../../outside")
        with self.assertRaises(ValueError):
            publish(self.write_envelope(bad_path), self.repo, self.key)
        self.assertFalse((self.root / "outside").exists())

        bad_digest = envelope()
        bad_digest["package"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "WASM digest"):
            publish(self.write_envelope(bad_digest), self.repo, self.key)


if __name__ == "__main__":
    unittest.main()
