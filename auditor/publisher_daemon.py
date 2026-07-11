#!/usr/bin/env python3
"""Consume audited envelopes with the narrow publisher identity."""

from __future__ import annotations
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import tomllib


def secure_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def save_status(path: pathlib.Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as destination:
            json.dump(status, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_once(config: dict) -> int:
    incoming = secure_dir(pathlib.Path(config["spool_in"]))
    processed = secure_dir(pathlib.Path(config["spool_processed"]))
    quarantined = secure_dir(pathlib.Path(config["spool_quarantine"]))
    failures = 0
    published_now = 0
    quarantined_now = 0
    for envelope in sorted(incoming.glob("*.json")):
        completed = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).with_name("publisher.py")), str(envelope), "--config", config["config_path"]],
            text=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(pathlib.Path.home()), "LANG": "C.UTF-8"},
        )
        if completed.returncode != 0:
            failures += 1
            continue
        # Automated pass envelopes are removed only after push + PR comment
        # succeed. Quarantine/reject reports remain available for manual review.
        disposition = json.loads(envelope.read_text()).get("disposition")
        destination = processed if disposition == "publish" else quarantined
        os.replace(envelope, destination / envelope.name)
        if disposition == "publish":
            published_now += 1
        else:
            quarantined_now += 1
    save_status(
        incoming.parent / "publisher-status.json",
        {
            "checkedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "failures": failures,
            "pending": len(list(incoming.glob("*.json"))),
            "published": published_now,
            "quarantined": quarantined_now,
        },
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", type=pathlib.Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    config["config_path"] = str(args.config.resolve())
    failures = run_once(config)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
