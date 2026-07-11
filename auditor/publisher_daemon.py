#!/usr/bin/env python3
"""Consume audited envelopes with the narrow publisher identity."""

from __future__ import annotations
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tomllib


def secure_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", type=pathlib.Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    incoming = secure_dir(pathlib.Path(config["spool_in"]))
    processed = secure_dir(pathlib.Path(config["spool_processed"]))
    quarantined = secure_dir(pathlib.Path(config["spool_quarantine"]))
    failures = 0
    for envelope in sorted(incoming.glob("*.json")):
        completed = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).with_name("publisher.py")), str(envelope), "--config", str(args.config)],
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
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
