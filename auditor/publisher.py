#!/usr/bin/env python3
"""Narrow publisher: consumes audited envelopes, updates this repo, signs index."""

from __future__ import annotations
import argparse
import base64
import datetime
import json
import os
import pathlib
import subprocess
import tomllib


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def publish(
    envelope_path: pathlib.Path,
    repo: pathlib.Path,
    key: pathlib.Path,
    approve_quarantine: bool = False,
    approval_note: str = "",
) -> tuple[str, str, int | None]:
    envelope = json.loads(envelope_path.read_text())
    pr = envelope.get("pullRequest", {}).get("number")
    disposition = envelope.get("disposition", "quarantine")
    if approve_quarantine and disposition == "quarantine":
        checker = envelope.get("checker", {})
        ai = envelope.get("aiReview", {})
        if checker.get("status") != "passed" or ai.get("disposition") != "pass" or ai.get("uncertain"):
            raise RuntimeError("manual approval requires deterministic pass and certain AI pass")
        envelope["manualApproval"] = {
            "by": "Sol (working on Martin's behalf)",
            "note": approval_note,
            "approvedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        disposition = "publish"
    if disposition != "publish":
        return disposition, "Automated audit quarantined this version. See the attached structured report.", pr
    submission, package = envelope["submission"], envelope["package"]
    pid, version = submission["pluginId"], submission["version"]
    manifest = package["manifest"]
    package_dir = repo / "packages" / pid / version
    audit_dir = repo / "audits" / pid
    package_dir.mkdir(parents=True, exist_ok=True); audit_dir.mkdir(parents=True, exist_ok=True)
    wasm = base64.b64decode(package["wasmBase64"], validate=True)
    files = {
        package_dir / "manifest.json": canonical(manifest),
        package_dir / "plugin.wasm": wasm,
        audit_dir / f"{version}.json": canonical({key: value for key, value in envelope.items() if key != "package"}),
    }
    for path, data in files.items():
        if path.exists() and path.read_bytes() != data:
            raise RuntimeError(f"immutable registry artifact differs: {path}")
        path.write_bytes(data)
    index_path = repo / "index.json"
    index = json.loads(index_path.read_text())
    plugins = {item["id"]: item for item in index["plugins"]}
    plugin = plugins.setdefault(pid, {
        "id": pid, "name": manifest["name"], "description": manifest["description"],
        "source": manifest["source"], "license": manifest["license"],
        "aiDevelopment": manifest.get("aiDevelopment", "none"), "versions": [],
    })
    if not any(item["version"] == version for item in plugin["versions"]):
        base = f"https://raw.githubusercontent.com/martinkoutecky/tine-plugin-registry/main"
        plugin["versions"].append({
            "version": version, "apiVersion": manifest["apiVersion"],
            "platforms": manifest.get("platforms", ["desktop"]), "capabilities": manifest["capabilities"],
            "sha256": package["sha256"],
            "manifestUrl": f"{base}/packages/{pid}/{version}/manifest.json",
            "wasmUrl": f"{base}/packages/{pid}/{version}/plugin.wasm",
            "audit": {"status": "passed", "url": f"{base}/audits/{pid}/{version}.json"},
            "publishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
    index["plugins"] = sorted(plugins.values(), key=lambda item: item["id"])
    index["generatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    index_bytes = canonical(index)
    index_path.write_bytes(index_bytes)
    raw_signature = repo / ".index.sig.raw"
    run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(index_path), "-out", str(raw_signature)])
    (repo / "index.json.sig").write_text(base64.b64encode(raw_signature.read_bytes()).decode() + "\n")
    raw_signature.unlink()
    return "published", f"Automated checks passed and {pid}@{version} was published.", pr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope", type=pathlib.Path)
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--approve-quarantine", action="store_true")
    parser.add_argument("--approval-note", default="")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    repo, key = pathlib.Path(config["registry_checkout"]), pathlib.Path(config["signing_key"])
    disposition, comment, pr = publish(
        args.envelope,
        repo,
        key,
        approve_quarantine=args.approve_quarantine,
        approval_note=args.approval_note,
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "GH_CONFIG_DIR": config["publisher_gh_config_dir"], "GH_PROMPT_DISABLED": "1"}
    if disposition == "published":
        run(["git", "-C", str(repo), "add", "index.json", "index.json.sig", "packages", "audits"])
        if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode != 0:
            run(["git", "-C", str(repo), "commit", "-m", "registry: publish audited submission"])
            run(["git", "-C", str(repo), "push", "origin", "main"], env=env)
    if pr is not None:
        run(["gh", "pr", "comment", str(pr), "--repo", config["registry"], "--body", comment], env=env)


if __name__ == "__main__":
    main()
