#!/usr/bin/env python3
"""Loopback-only bootstrap for the narrow registry publisher GitHub App."""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import secrets
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


APP_NAME = "Tine Plugin Registry Publisher"
APP_HOMEPAGE = "https://github.com/martinkoutecky/tine-plugin-registry"
KEY_PATH = pathlib.Path.home() / ".config/tine-plugin-auditor/github-app.pem"
METADATA_PATH = pathlib.Path.home() / ".config/tine-plugin-auditor/github-app.json"
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


def app_manifest(redirect_url: str) -> dict:
    return {
        "name": APP_NAME,
        "url": APP_HOMEPAGE,
        "description": "Short-lived-token publisher for Tine's signed community plugin registry.",
        "redirect_url": redirect_url,
        "hook_attributes": {
            "url": "https://example.invalid/tine-plugin-registry-unused-webhook",
            "active": False,
        },
        "public": False,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
        },
        "default_events": [],
        "request_oauth_on_install": False,
    }


def exchange_manifest_code(code: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", code):
        raise RuntimeError("GitHub returned an invalid manifest code")
    request = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions",
        data=b"",
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tine-plugin-publisher-bootstrap/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub manifest response exceeded the bootstrap limit")
    result = json.loads(body)
    if not isinstance(result, dict):
        raise RuntimeError("GitHub returned an invalid manifest response")
    return result


def validate_created_app(result: dict) -> tuple[int, str, str]:
    app_id = result.get("id")
    slug = result.get("slug")
    pem = result.get("pem")
    permissions = result.get("permissions")
    events = result.get("events")
    if not isinstance(app_id, int) or app_id <= 0:
        raise RuntimeError("GitHub App ID is invalid")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise RuntimeError("GitHub App slug is invalid")
    if (
        not isinstance(pem, str)
        or not re.search(r"-----BEGIN (?:RSA )?PRIVATE KEY-----", pem)
        or not re.search(r"-----END (?:RSA )?PRIVATE KEY-----\s*$", pem)
    ):
        raise RuntimeError("GitHub App private key is invalid")
    if not isinstance(permissions, dict):
        raise RuntimeError("GitHub App permissions are missing")
    if permissions.get("contents") != "write" or permissions.get("pull_requests") != "write":
        raise RuntimeError("GitHub App does not have the required narrow permissions")
    allowed = {"contents", "pull_requests", "metadata"}
    unexpected = {key for key, value in permissions.items() if key not in allowed and value not in (None, "none")}
    if unexpected:
        raise RuntimeError(f"GitHub App received unexpected permissions: {', '.join(sorted(unexpected))}")
    if events != []:
        raise RuntimeError("GitHub App unexpectedly subscribes to webhook events")
    return app_id, slug, pem


def write_new_private_file(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def persist_created_app(result: dict, key_path: pathlib.Path, metadata_path: pathlib.Path) -> tuple[int, str]:
    app_id, slug, pem = validate_created_app(result)
    metadata = json.dumps({"appId": app_id, "slug": slug}, indent=2, sort_keys=True).encode() + b"\n"
    write_new_private_file(key_path, pem.encode())
    try:
        write_new_private_file(metadata_path, metadata)
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise
    return app_id, slug


class BootstrapServer(ThreadingHTTPServer):
    state: str
    callback_path: str
    key_path: pathlib.Path
    metadata_path: pathlib.Path
    completed: bool = False
    error: str | None = None


class BootstrapHandler(BaseHTTPRequestHandler):
    server: BootstrapServer
    server_version = "TinePublisherBootstrap/0.1"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_html(self, status: int, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; style-src 'unsafe-inline'; form-action https://github.com",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            port = self.server.server_address[1]
            redirect = f"http://127.0.0.1:{port}{self.server.callback_path}"
            manifest = html.escape(json.dumps(app_manifest(redirect), separators=(",", ":")), quote=True)
            state = urllib.parse.quote(self.server.state, safe="")
            self.send_html(
                200,
                "<!doctype html><meta charset=utf-8><title>Create Tine publisher App</title>"
                "<style>body{font:16px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem;line-height:1.5}"
                "button{font:inherit;padding:.65rem 1rem}</style>"
                "<h1>Create the Tine registry publisher</h1>"
                "<p>GitHub will show the exact permissions before creation: Contents and Pull requests, read/write. "
                "The App is private, has no active webhook, subscribes to no events, and requests no user authorization.</p>"
                f'<form action="https://github.com/settings/apps/new?state={state}" method="post">'
                f'<input type="hidden" name="manifest" value="{manifest}">'
                '<button type="submit">Review and create on GitHub</button></form>',
            )
            return
        if parsed.path != self.server.callback_path:
            self.send_html(404, "<h1>Not found</h1>")
            return
        try:
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        except ValueError:
            self.send_html(400, "<h1>Invalid callback</h1>")
            return
        if query.get("state") != [self.server.state] or len(query.get("code", [])) != 1:
            self.send_html(400, "<h1>Invalid callback</h1><p>The bootstrap state did not match.</p>")
            return
        try:
            result = exchange_manifest_code(query["code"][0])
            app_id, slug = persist_created_app(result, self.server.key_path, self.server.metadata_path)
            install_url = html.escape(f"https://github.com/apps/{slug}/installations/new", quote=True)
            self.server.completed = True
            self.send_html(
                200,
                "<!doctype html><meta charset=utf-8><title>Tine publisher created</title>"
                "<style>body{font:16px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem;line-height:1.5}"
                "a{display:inline-block;padding:.65rem 1rem;background:#1769d2;color:white;text-decoration:none;border-radius:.4rem}</style>"
                f"<h1>Publisher App created</h1><p>App ID {app_id}; its private key was stored mode 600 locally.</p>"
                f'<p><a href="{install_url}">Install on the registry</a></p>'
                "<p>Choose <strong>Only select repositories</strong> and select only "
                "<strong>tine-plugin-registry</strong>. Then return to Codex.</p>",
            )
        except Exception as error:  # keep secrets out of browser and terminal output
            self.server.error = type(error).__name__
            self.send_html(500, "<h1>Bootstrap failed safely</h1><p>No credential was activated. Return to Codex.</p>")
        finally:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=pathlib.Path, default=KEY_PATH)
    parser.add_argument("--metadata", type=pathlib.Path, default=METADATA_PATH)
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: choose an available port)")
    args = parser.parse_args()
    if args.port != 0 and not 1024 <= args.port <= 65535:
        raise SystemExit("port must be 0 or between 1024 and 65535")
    if args.key.exists() or args.metadata.exists():
        raise SystemExit("refusing to overwrite an existing GitHub App bootstrap file")
    server = BootstrapServer(("127.0.0.1", args.port), BootstrapHandler)
    server.state = secrets.token_urlsafe(32)
    server.callback_path = f"/callback/{secrets.token_urlsafe(32)}"
    server.key_path = args.key
    server.metadata_path = args.metadata
    print(f"Open http://127.0.0.1:{server.server_address[1]}/ in a browser.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        raise SystemExit("GitHub App bootstrap cancelled") from None
    finally:
        server.server_close()
    if not server.completed:
        raise SystemExit(f"GitHub App bootstrap did not complete ({server.error or 'invalid callback'})")
    print(f"Saved GitHub App metadata to {args.metadata} and the private key to {args.key}.")


if __name__ == "__main__":
    main()
