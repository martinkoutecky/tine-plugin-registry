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


def gh(config: dict, args: list[str]) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(pathlib.Path.home())),
        "GH_CONFIG_DIR": config["gh_config_dir"],
        "GH_PROMPT_DISABLED": "1",
    }
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, env=env).stdout


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
    prs = json.loads(gh(config, ["pr", "list", "--repo", config["registry"], "--state", "open", "--limit", "100", "--json", "number,headRefOid,files"]))
    completed_now = 0
    for pr in prs:
        lease = f"{pr['number']}@{pr['headRefOid']}"
        if lease in state["completed"]:
            continue
        paths = [item["path"] for item in pr.get("files", [])]
        submissions = [path for path in paths if path.startswith("submissions/") and path.endswith(".json")]
        if len(submissions) != 1 or any(not path.startswith("submissions/") for path in paths):
            state["completed"][lease] = {"status": "ignored", "reason": "PR must change exactly one submission file"}
            save_state(state_path, state)
            continue
        encoded = json.loads(gh(config, ["api", f"repos/{config['registry']}/contents/{submissions[0]}?ref={pr['headRefOid']}"]))["content"]
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
                envelope["pullRequest"] = {"number": pr["number"], "head": pr["headRefOid"]}
            except Exception as exc:
                envelope = {
                    "format": "tine-plugin-audit-result/v1",
                    "pullRequest": {"number": pr["number"], "head": pr["headRefOid"]},
                    "disposition": "quarantine",
                    "failure": f"auditor failed closed: {type(exc).__name__}: {exc}",
                }
            outgoing = spool / f"pr-{pr['number']}-{pr['headRefOid']}.json"
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
