import base64
import datetime
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import github_app


def decode_part(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class GithubAppTests(unittest.TestCase):
    def test_app_jwt_is_short_lived_and_rsa_signed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            private, public = root / "private.pem", root / "public.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private)], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
            token = github_app.app_jwt(12345, private, now=1_700_000_000)
            header, payload, signature = token.split(".")
            self.assertEqual(json.loads(decode_part(header)), {"alg": "RS256", "typ": "JWT"})
            self.assertEqual(json.loads(decode_part(payload)), {"iat": 1_699_999_940, "exp": 1_700_000_540, "iss": "12345"})
            signature_path = root / "signature"
            signature_path.write_bytes(decode_part(signature))
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signature_path)],
                input=f"{header}.{payload}".encode(),
                capture_output=True,
            )
            self.assertEqual(verified.returncode, 0)

    def test_installation_token_is_checked_for_shape_and_expiry(self):
        expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        response = {"token": "x" * 40, "expires_at": expiry, "permissions": {"contents": "write"}}
        with mock.patch.object(github_app, "github_json", return_value=response):
            with mock.patch.object(github_app, "app_jwt", return_value="jwt"):
                self.assertEqual(github_app.installation_token(1, 2, pathlib.Path("key")), "x" * 40)
        with mock.patch.object(
            github_app,
            "github_json",
            return_value={"token": "short", "expires_at": expiry, "permissions": {}},
        ):
            with mock.patch.object(github_app, "app_jwt", return_value="jwt"):
                with self.assertRaises(RuntimeError):
                    github_app.installation_token(1, 2, pathlib.Path("key"))


if __name__ == "__main__":
    unittest.main()
