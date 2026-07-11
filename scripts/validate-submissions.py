#!/usr/bin/env python3
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{1,62}[a-z0-9])$")
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
REPO = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ALLOWED = {"schemaVersion", "pluginId", "version", "repository", "commit", "manifestPath"}

errors = []
seen = set()
for path in sorted((ROOT / "submissions").glob("*/*.json")):
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        continue
    unknown = set(value) - ALLOWED if isinstance(value, dict) else {"not-object"}
    if unknown:
        errors.append(f"{path.relative_to(ROOT)}: unknown fields {sorted(unknown)}")
        continue
    pid, version = value.get("pluginId"), value.get("version")
    expected = pathlib.Path("submissions") / str(pid) / f"{version}.json"
    if path.relative_to(ROOT) != expected:
        errors.append(f"{path.relative_to(ROOT)}: path must be {expected}")
    checks = [
        (value.get("schemaVersion") == 1, "schemaVersion must be 1"),
        (isinstance(pid, str) and ID.fullmatch(pid), "invalid pluginId"),
        (isinstance(version, str) and VERSION.fullmatch(version), "invalid version"),
        (isinstance(value.get("repository"), str) and REPO.fullmatch(value["repository"]), "repository must be a GitHub HTTPS URL"),
        (isinstance(value.get("commit"), str) and COMMIT.fullmatch(value["commit"]), "commit must be a full lowercase SHA"),
    ]
    manifest = value.get("manifestPath")
    checks.append((isinstance(manifest, str) and not manifest.startswith("/") and ".." not in pathlib.PurePosixPath(manifest).parts and manifest.endswith("manifest.json"), "invalid manifestPath"))
    for ok, message in checks:
        if not ok:
            errors.append(f"{path.relative_to(ROOT)}: {message}")
    key = (pid, version)
    if key in seen:
        errors.append(f"{path.relative_to(ROOT)}: duplicate id/version")
    seen.add(key)

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print(f"validated {len(seen)} immutable submission(s)")
