#!/usr/bin/env python3
"""Verify the installed GitHub App scope and create the local publisher config."""

from __future__ import annotations

import argparse
import json
import pathlib

from bootstrap_github_app import KEY_PATH, METADATA_PATH, write_new_private_file
from github_app import app_jwt, github_json, installation_credentials
from publisher import private_file


REPOSITORY = "martinkoutecky/tine-plugin-registry"
EXPECTED_SLUG = "tine-plugin-registry-publisher"
EXPECTED_PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}
DEFAULT_CONFIG = pathlib.Path(__file__).with_name("publisher-config.local.toml")
SIGNING_KEY = pathlib.Path.home() / ".config/tine-plugin-auditor/registry-ed25519.pem"
STATE_ROOT = pathlib.Path.home() / ".local/state/tine-plugin-auditor"


def exact_permissions(value: object, where: str) -> None:
    if not isinstance(value, dict):
        raise RuntimeError(f"{where} permissions are missing")
    active = {key: permission for key, permission in value.items() if permission not in (None, "none")}
    if active != EXPECTED_PERMISSIONS:
        raise RuntimeError(f"{where} permissions are not exactly the approved narrow set")


def load_metadata(path: pathlib.Path) -> tuple[int, str]:
    value = json.loads(private_file(path, "GitHub App metadata").read_text())
    if not isinstance(value, dict) or set(value) != {"appId", "slug"}:
        raise RuntimeError("GitHub App metadata is invalid")
    app_id, slug = value["appId"], value["slug"]
    if not isinstance(app_id, int) or app_id <= 0 or slug != EXPECTED_SLUG:
        raise RuntimeError("GitHub App metadata identity is invalid")
    return app_id, slug


def discover_installation(app_id: int, slug: str, key: pathlib.Path) -> int:
    jwt = app_jwt(app_id, key)
    app = github_json("https://api.github.com/app", "GET", jwt)
    if not isinstance(app, dict) or app.get("id") != app_id or app.get("slug") != slug:
        raise RuntimeError("authenticated GitHub App identity does not match local metadata")
    owner = app.get("owner")
    if not isinstance(owner, dict) or owner.get("login") != "martinkoutecky":
        raise RuntimeError("GitHub App is not owned by martinkoutecky")
    exact_permissions(app.get("permissions"), "GitHub App")
    if app.get("events") != []:
        raise RuntimeError("GitHub App unexpectedly subscribes to webhook events")

    installations = github_json("https://api.github.com/app/installations?per_page=100", "GET", jwt)
    if not isinstance(installations, list):
        raise RuntimeError("GitHub App installations response is invalid")
    matching = []
    for installation in installations:
        if not isinstance(installation, dict):
            continue
        account = installation.get("account")
        if isinstance(account, dict) and account.get("login") == "martinkoutecky":
            matching.append(installation)
    if len(matching) != 1:
        raise RuntimeError("GitHub App must have exactly one martinkoutecky installation")
    installation = matching[0]
    installation_id = installation.get("id")
    if not isinstance(installation_id, int) or installation_id <= 0:
        raise RuntimeError("GitHub App installation ID is invalid")
    if installation.get("repository_selection") != "selected":
        raise RuntimeError("GitHub App installation is not restricted to selected repositories")
    exact_permissions(installation.get("permissions"), "GitHub App installation")

    token, token_permissions, token_repositories = installation_credentials(app_id, installation_id, key)
    exact_permissions(token_permissions, "GitHub App token")
    if token_repositories is not None and (
        len(token_repositories) != 1
        or not isinstance(token_repositories[0], dict)
        or token_repositories[0].get("full_name") != REPOSITORY
    ):
        raise RuntimeError("GitHub App token response is not restricted to tine-plugin-registry")
    repositories = github_json("https://api.github.com/installation/repositories?per_page=100", "GET", token)
    if not isinstance(repositories, dict) or repositories.get("total_count") != 1:
        raise RuntimeError("GitHub App token is not restricted to exactly one repository")
    items = repositories.get("repositories")
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or items[0].get("full_name") != REPOSITORY
    ):
        raise RuntimeError("GitHub App token does not target only tine-plugin-registry")
    return installation_id


def render_config(app_id: int, installation_id: int, key: pathlib.Path) -> bytes:
    values = {
        "registry": REPOSITORY,
        "registry_checkout": str(pathlib.Path(__file__).resolve().parents[1]),
        "signing_key": str(SIGNING_KEY),
        "github_app_id": app_id,
        "github_installation_id": installation_id,
        "github_app_private_key": str(key),
        "spool_in": str(STATE_ROOT / "outgoing"),
        "spool_processed": str(STATE_ROOT / "processed"),
        "spool_quarantine": str(STATE_ROOT / "quarantine"),
    }
    lines = []
    for name, value in values.items():
        lines.append(f"{name} = {value}" if isinstance(value, int) else f"{name} = {json.dumps(value)}")
    return ("\n".join(lines) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=pathlib.Path, default=METADATA_PATH)
    parser.add_argument("--key", type=pathlib.Path, default=KEY_PATH)
    parser.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    parser.add_argument("--verify-only", action="store_true", help="verify live scope without writing configuration")
    args = parser.parse_args()
    app_id, slug = load_metadata(args.metadata)
    key = private_file(args.key, "GitHub App private key")
    private_file(SIGNING_KEY, "registry signing key")
    installation_id = discover_installation(app_id, slug, key)
    if args.verify_only:
        print(f"Verified GitHub App {app_id}, installation {installation_id}, repository {REPOSITORY}.")
        return
    for directory in (STATE_ROOT / "outgoing", STATE_ROOT / "processed", STATE_ROOT / "quarantine"):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    write_new_private_file(args.config, render_config(app_id, installation_id, key))
    print(f"Verified GitHub App {app_id}, installation {installation_id}, repository {REPOSITORY}.")
    print(f"Wrote mode-600 publisher configuration to {args.config}.")


if __name__ == "__main__":
    main()
