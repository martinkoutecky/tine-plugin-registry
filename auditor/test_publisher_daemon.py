import json
import pathlib
import tempfile
import unittest
from unittest import mock

import publisher_daemon


class PublisherDaemonTests(unittest.TestCase):
    def test_cycle_moves_successes_and_records_credential_free_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = {
                "config_path": str(root / "config.toml"),
                "spool_in": str(root / "outgoing"),
                "spool_processed": str(root / "processed"),
                "spool_quarantine": str(root / "quarantine"),
            }
            incoming = pathlib.Path(config["spool_in"])
            incoming.mkdir()
            (incoming / "published.json").write_text(json.dumps({"disposition": "publish"}))
            (incoming / "rejected.json").write_text(json.dumps({"disposition": "reject"}))
            with mock.patch.object(publisher_daemon.subprocess, "run", return_value=mock.Mock(returncode=0)):
                self.assertEqual(publisher_daemon.run_once(config), 0)
            self.assertTrue((pathlib.Path(config["spool_processed"]) / "published.json").is_file())
            self.assertTrue((pathlib.Path(config["spool_quarantine"]) / "rejected.json").is_file())
            status = json.loads((root / "publisher-status.json").read_text())
            self.assertEqual(status["failures"], 0)
            self.assertEqual(status["pending"], 0)
            self.assertEqual(status["published"], 1)
            self.assertEqual(status["quarantined"], 1)
            self.assertNotIn("token", json.dumps(status).lower())

    def test_failed_envelope_remains_pending_for_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = {
                "config_path": str(root / "config.toml"),
                "spool_in": str(root / "outgoing"),
                "spool_processed": str(root / "processed"),
                "spool_quarantine": str(root / "quarantine"),
            }
            incoming = pathlib.Path(config["spool_in"])
            incoming.mkdir()
            (incoming / "retry.json").write_text(json.dumps({"disposition": "publish"}))
            with mock.patch.object(publisher_daemon.subprocess, "run", return_value=mock.Mock(returncode=1)):
                self.assertEqual(publisher_daemon.run_once(config), 1)
            self.assertTrue((incoming / "retry.json").is_file())
            status = json.loads((root / "publisher-status.json").read_text())
            self.assertEqual(status["failures"], 1)
            self.assertEqual(status["pending"], 1)


if __name__ == "__main__":
    unittest.main()
