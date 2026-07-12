#!/usr/bin/env python3
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "auditor"))
from validation import submission_id, validate_submission

errors = []
seen = set()
for path in sorted((ROOT / "submissions").glob("*/*.json")):
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        continue
    try:
        submission = validate_submission(value)
    except ValueError as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        continue
    pid, version = submission_id(submission), submission["version"]
    expected = pathlib.Path("submissions") / str(pid) / f"{version}.json"
    if path.relative_to(ROOT) != expected:
        errors.append(f"{path.relative_to(ROOT)}: path must be {expected}")
    key = (pid, version)
    if key in seen:
        errors.append(f"{path.relative_to(ROOT)}: duplicate id/version")
    seen.add(key)

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print(f"validated {len(seen)} immutable submission(s)")
