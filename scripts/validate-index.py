#!/usr/bin/env python3
"""Verify the checked-in signed index and every local immutable artifact."""

import base64
import hashlib
import json
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "https://raw.githubusercontent.com/martinkoutecky/tine-plugin-registry/main"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def main() -> None:
    index_path = ROOT / "index.json"
    raw = index_path.read_bytes()
    index = json.loads(raw)
    if raw != canonical(index):
        raise SystemExit("index.json is not canonical sorted JSON")
    if index.get("schemaVersion") != 1 or set(index) != {"schemaVersion", "generatedAt", "plugins", "revocations"}:
        raise SystemExit("index.json envelope is invalid")

    identities = set()
    for plugin in index["plugins"]:
        plugin_id = plugin["id"]
        for version in plugin["versions"]:
            identity = (plugin_id, version["version"])
            if identity in identities:
                raise SystemExit(f"duplicate registry version: {identity}")
            identities.add(identity)
            package = ROOT / "packages" / plugin_id / version["version"]
            manifest_path, wasm_path = package / "manifest.json", package / "plugin.wasm"
            manifest_bytes, wasm_bytes = manifest_path.read_bytes(), wasm_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            if manifest_bytes != canonical(manifest):
                raise SystemExit(f"manifest is not canonical: {identity}")
            if manifest.get("id") != plugin_id or manifest.get("version") != version["version"]:
                raise SystemExit(f"manifest identity mismatch: {identity}")
            if manifest.get("apiVersion") != version["apiVersion"]:
                raise SystemExit(f"manifest API mismatch: {identity}")
            if manifest.get("platforms", ["desktop"]) != version["platforms"]:
                raise SystemExit(f"manifest platform mismatch: {identity}")
            if manifest.get("capabilities") != version["capabilities"]:
                raise SystemExit(f"manifest capability mismatch: {identity}")
            if hashlib.sha256(manifest_bytes).hexdigest() != version["manifestSha256"]:
                raise SystemExit(f"manifest digest mismatch: {identity}")
            if hashlib.sha256(wasm_bytes).hexdigest() != version["sha256"]:
                raise SystemExit(f"WASM digest mismatch: {identity}")
            expected = f"{BASE}/packages/{plugin_id}/{version['version']}"
            if version["manifestUrl"] != f"{expected}/manifest.json" or version["wasmUrl"] != f"{expected}/plugin.wasm":
                raise SystemExit(f"artifact URL mismatch: {identity}")
            audit = ROOT / "audits" / plugin_id / f"{version['version']}.json"
            audit_meta = version.get("audit", {})
            if audit_meta.get("status") != "passed" or not audit.is_file():
                raise SystemExit(f"passing audit is missing: {identity}")
            audit_bytes = audit.read_bytes()
            audit_value = json.loads(audit_bytes)
            if audit_bytes != canonical(audit_value):
                raise SystemExit(f"audit is not canonical: {identity}")
            if hashlib.sha256(audit_bytes).hexdigest() != audit_meta.get("sha256"):
                raise SystemExit(f"audit digest mismatch: {identity}")
            checker = audit_value.get("checker", {})
            if (
                checker.get("risk") != audit_meta.get("risk")
                or checker.get("checkedAt") != audit_meta.get("checkedAt")
                or audit_value.get("disposition") != audit_meta.get("automatedDisposition")
                or ("manualApproval" in audit_value) != audit_meta.get("manualApproval")
            ):
                raise SystemExit(f"signed audit summary mismatch: {identity}")

    signature = base64.b64decode((ROOT / "index.json.sig").read_text().strip(), validate=True)
    with tempfile.NamedTemporaryFile() as sig:
        sig.write(signature)
        sig.flush()
        completed = subprocess.run(
            [
                "openssl", "pkeyutl", "-verify", "-pubin",
                "-inkey", str(ROOT / "registry-ed25519.pub.pem"),
                "-rawin", "-in", str(index_path), "-sigfile", sig.name,
            ],
            capture_output=True,
            text=True,
        )
    if completed.returncode != 0:
        raise SystemExit("index.json signature is invalid")
    print(f"verified signed index with {len(identities)} immutable version(s)")


if __name__ == "__main__":
    main()
