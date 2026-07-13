#!/usr/bin/env python3
"""Start, stop, and inspect the local Tine plugin audit scheduler."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import tomllib


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def paths(config_path: pathlib.Path) -> tuple[dict, pathlib.Path, pathlib.Path, pathlib.Path]:
    config = tomllib.loads(config_path.read_text())
    root = pathlib.Path(config["state_root"]).expanduser()
    return config, root / "supervisor.pid", root / "supervisor.log", root / "supervisor-status.json"


def read_pid(path: pathlib.Path) -> int | None:
    try:
        value = int(path.read_text().strip())
        return value if value > 1 else None
    except (FileNotFoundError, ValueError):
        return None


def start(config_path: pathlib.Path) -> None:
    _, pid_path, log_path, _ = paths(config_path)
    pid = read_pid(pid_path)
    if pid and process_alive(pid):
        print(f"auditor supervisor already running as PID {pid}")
        return
    pid_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pid_path.parent.chmod(0o700)
    log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    process = subprocess.Popen(
        [sys.executable, str(pathlib.Path(__file__).with_name("supervisor.py")), "--config", str(config_path.resolve())],
        stdin=subprocess.DEVNULL,
        stdout=log_descriptor,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    os.close(log_descriptor)
    temporary = pid_path.with_suffix(".tmp")
    temporary.write_text(f"{process.pid}\n")
    temporary.chmod(0o600)
    os.replace(temporary, pid_path)
    time.sleep(0.5)
    if process.poll() is not None:
        pid_path.unlink(missing_ok=True)
        raise SystemExit(f"auditor supervisor exited during startup; inspect {log_path}")
    print(f"started auditor supervisor as PID {process.pid}; log: {log_path}")


def stop(config_path: pathlib.Path) -> None:
    _, pid_path, _, _ = paths(config_path)
    pid = read_pid(pid_path)
    if not pid or not process_alive(pid):
        pid_path.unlink(missing_ok=True)
        print("auditor supervisor is not running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not process_alive(pid):
            pid_path.unlink(missing_ok=True)
            print("auditor supervisor stopped")
            return
        time.sleep(0.1)
    raise SystemExit("auditor supervisor did not stop within five seconds")


def status(config_path: pathlib.Path, as_json: bool) -> None:
    config, pid_path, log_path, status_path = paths(config_path)
    pid = read_pid(pid_path)
    value = json.loads(status_path.read_text()) if status_path.is_file() else {}
    running = bool(pid and process_alive(pid))
    workers = value.get("workers", {}) if isinstance(value, dict) else {}
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = True
    for name, worker in workers.items():
        if not isinstance(worker, dict):
            fresh = False
            continue
        timestamp = worker.get("lastStartedAt") if worker.get("running") else worker.get("lastCompletedAt")
        try:
            age = (now - datetime.datetime.fromisoformat(timestamp)).total_seconds()
        except (TypeError, ValueError):
            fresh = False
            continue
        limit = int(config.get(f"{name}_timeout_seconds", 300)) + 60 if worker.get("running") else int(
            config.get(f"{name}_interval_seconds", 300)
        ) * 2 + 60
        fresh = fresh and age <= limit
    healthy = running and len(workers) == 2 and fresh and all(
        worker.get("running") or worker.get("exitCode") == 0
        for worker in workers.values() if isinstance(worker, dict)
    )
    report = {"running": running, "healthy": healthy, "pid": pid, "log": str(log_path), "scheduler": value}
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"running: {running}; healthy: {healthy}; pid: {pid or '-'}")
        for name, worker in sorted(workers.items()):
            print(f"{name}: exit={worker.get('exitCode')} completed={worker.get('lastCompletedAt', '-')} error={worker.get('error', '-')}")
        print(f"log: {log_path}")
    if not healthy:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "start":
        start(args.config)
    elif args.command == "stop":
        stop(args.config)
    elif args.command == "restart":
        stop(args.config)
        start(args.config)
    else:
        status(args.config, args.json)


if __name__ == "__main__":
    main()
