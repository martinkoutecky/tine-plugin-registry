import json
import pathlib
import stat
import tempfile
import unittest

from bootstrap_github_app import app_manifest, persist_created_app, private_root, validate_created_app


def result(**updates):
    value = {
        "id": 12345,
        "slug": "tine-plugin-registry-publisher",
        "pem": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----\n",
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
        "events": [],
        "client_secret": "must-not-be-stored",
        "webhook_secret": "must-not-be-stored",
    }
    value.update(updates)
    return value


class BootstrapGithubAppTests(unittest.TestCase):
    def test_private_root_can_target_a_host_backed_mount(self):
        self.assertEqual(
            private_root({"TINE_PLUGIN_PRIVATE_ROOT": "/aux/private/tine"}),
            pathlib.Path("/aux/private/tine"),
        )

    def test_manifest_requests_only_the_narrow_noninteractive_app(self):
        manifest = app_manifest("http://127.0.0.1:1234/callback/secret")
        self.assertEqual(manifest["default_permissions"], {"contents": "write", "pull_requests": "write"})
        self.assertEqual(manifest["default_events"], [])
        self.assertFalse(manifest["hook_attributes"]["active"])
        self.assertFalse(manifest["request_oauth_on_install"])
        self.assertFalse(manifest["public"])

    def test_response_rejects_permission_or_webhook_expansion(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected permissions"):
            validate_created_app(result(permissions={"contents": "write", "pull_requests": "write", "issues": "write"}))
        with self.assertRaisesRegex(RuntimeError, "webhook events"):
            validate_created_app(result(events=["push"]))

    def test_persistence_is_mode_600_atomic_and_drops_other_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            key, metadata = root / "private/key.pem", root / "private/app.json"
            self.assertEqual(persist_created_app(result(), key, metadata), (12345, "tine-plugin-registry-publisher"))
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(metadata.stat().st_mode), 0o600)
            self.assertNotIn("must-not-be-stored", metadata.read_text())
            self.assertEqual(json.loads(metadata.read_text()), {"appId": 12345, "slug": "tine-plugin-registry-publisher"})
            with self.assertRaises(FileExistsError):
                persist_created_app(result(), key, metadata)


if __name__ == "__main__":
    unittest.main()
