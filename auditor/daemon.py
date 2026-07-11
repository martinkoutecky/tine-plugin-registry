#!/usr/bin/env python3
"""Read-only GitHub poller. Produces result envelopes; never publishes them."""

from __future__ import annotations
import argparse
import base64
import json
import os
import pathlib
import subprocess
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request


def github_json(config: dict, route: str) -> object:
    url = f"https://api.github.com/repos/{config['registry']}/{route}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tine-plugin-auditor/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if int(response.headers.get("Content-Length", "0")) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub response is too large")
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub response is too large")
        return json.loads(data)


def load_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"completed": {}}


def save_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def audit_open_prs(config: dict) -> int:
    work_root = pathlib.Path(config["work_root"])
    spool = pathlib.Path(config["spool_out"])
    state_path = work_root / "state.json"
    work_root.mkdir(parents=True, exist_ok=True)
    spool.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)
    prs = github_json(config, "pulls?state=open&per_page=100")
    completed_now = 0
    for pr in prs:
        head = pr["head"]["sha"]
        lease = f"{pr['number']}@{head}"
        if lease in state["completed"]:
            continue
        files = github_json(config, f"pulls/{pr['number']}/files?per_page=100")
        paths = [item["filename"] for item in files]
        submissions = [path for path in paths if path.startswith("submissions/") and path.endswith(".json")]
        if len(submissions) != 1 or any(not path.startswith("submissions/") for path in paths):
            state["completed"][lease] = {"status": "ignored", "reason": "PR must change exactly one submission file"}
            save_state(state_path, state)
            continue
        quoted = urllib.parse.quote(submissions[0], safe="/")
        encoded = github_json(config, f"contents/{quoted}?ref={head}")["content"]
        with tempfile.TemporaryDirectory(prefix="tine-plugin-submission-") as temp:
            submission = pathlib.Path(temp) / "submission.json"
            submission.write_bytes(base64.b64decode(encoded))
            result = pathlib.Path(temp) / "result.json"
            command = [
                "python3", str(pathlib.Path(__file__).with_name("audit.py")), str(submission),
                "--tine", config["tine_checkout"], "--image", config["builder_image"],
                "--max-source-bytes", str(config.get("max_source_bytes", 524288)), "--output", str(result),
            ]
            try:
                subprocess.run(command, check=True, timeout=1800)
                envelope = json.loads(result.read_text())
                envelope["pullRequest"] = {"number": pr["number"], "head": head}
            except Exception as exc:
                envelope = {
                    "format": "tine-plugin-audit-result/v1",
                    "pullRequest": {"number": pr["number"], "head": head},
                    "disposition": "quarantine",
                    "failure": f"auditor failed closed: {type(exc).__name__}: {exc}",
                }
            outgoing = spool / f"pr-{pr['number']}-{head}.json"
            outgoing.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
        state["completed"][lease] = {"status": envelope["disposition"], "outgoing": outgoing.name}
        save_state(state_path, state)
        completed_now += 1
    return completed_now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    while True:
        audit_open_prs(config)
        if args.once:
            break
        time.sleep(config.get("poll_seconds", 300))


if __name__ == "__main__":
    main()
