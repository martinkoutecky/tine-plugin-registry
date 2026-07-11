"""Short-lived GitHub App installation tokens without a long-lived PAT."""

from __future__ import annotations
import base64
import datetime
import json
import pathlib
import subprocess
import time
import urllib.request


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def app_jwt(app_id: int, private_key: pathlib.Path, now: int | None = None) -> str:
    timestamp = int(time.time()) if now is None else now
    header = base64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = base64url(
        json.dumps({"iat": timestamp - 60, "exp": timestamp + 540, "iss": str(app_id)}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signed = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(private_key)],
        input=signing_input,
        capture_output=True,
        check=True,
    ).stdout
    return f"{header}.{payload}.{base64url(signed)}"


def github_json(url: str, method: str, bearer: str, body: object | None = None) -> object:
    encoded = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "User-Agent": "tine-plugin-publisher/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub response exceeded the publisher limit")
        return json.loads(data) if data else {}


def installation_token(app_id: int, installation_id: int, private_key: pathlib.Path) -> str:
    jwt = app_jwt(app_id, private_key)
    result = github_json(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        "POST",
        jwt,
        {},
    )
    token = result.get("token") if isinstance(result, dict) else None
    expires = result.get("expires_at") if isinstance(result, dict) else None
    if not isinstance(token, str) or len(token) < 20 or not isinstance(expires, str):
        raise RuntimeError("GitHub App did not return a valid installation token")
    expiry = datetime.datetime.fromisoformat(expires.replace("Z", "+00:00"))
    if expiry <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        raise RuntimeError("GitHub App returned an unexpectedly short-lived token")
    return token
