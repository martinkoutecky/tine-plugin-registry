#!/usr/bin/env python3
"""Run the credential-free auditor and narrow publisher on independent schedules."""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "w") as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Supervisor:
    def __init__(self, config: dict):
        self.config = config
        self.root = pathlib.Path(__file__).resolve().parent
        self.state_root = pathlib.Path(config["state_root"]).expanduser()
        self.status_path = self.state_root / "supervisor-status.json"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.status = {
            "format": "tine-plugin-auditor-supervisor/v1",
            "pid": os.getpid(),
            "startedAt": utc_now(),
            "updatedAt": utc_now(),
            "workers": {},
        }

    def save(self) -> None:
        with self.lock:
            self.status["updatedAt"] = utc_now()
            atomic_json(self.status_path, self.status)

    def worker(self, name: str, command: list[str], interval: int, timeout: int) -> None:
        while not self.stop.is_set():
            started = time.monotonic()
            with self.lock:
                self.status["workers"][name] = {
                    **self.status["workers"].get(name, {}),
                    "lastStartedAt": utc_now(),
                    "running": True,
                }
            self.save()
            result: dict[str, object]
            try:
                process = subprocess.Popen(command)
                deadline = time.monotonic() + timeout
                while process.poll() is None and not self.stop.wait(0.25) and time.monotonic() < deadline:
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    error = "supervisor stopping" if self.stop.is_set() else f"cycle exceeded {timeout} seconds"
                    result = {"exitCode": None, "error": error}
                else:
                    result = {"exitCode": process.returncode}
            except Exception as error:
                result = {"exitCode": None, "error": type(error).__name__}
            elapsed = round(time.monotonic() - started, 3)
            with self.lock:
                previous = self.status["workers"].get(name, {})
                if "error" not in result:
                    previous.pop("error", None)
                self.status["workers"][name] = {
                    **previous,
                    **result,
                    "lastCompletedAt": utc_now(),
                    "durationSeconds": elapsed,
                    "running": False,
                }
            self.save()
            if self.stop.wait(max(0, interval - elapsed)):
                return

    def run(self) -> None:
        python = sys.executable
        workers = [
            threading.Thread(
                name="tine-plugin-auditor",
                target=self.worker,
                args=(
                    "auditor",
                    [python, str(self.root / "daemon.py"), "--once", "--config", self.config["auditor_config"]],
                    int(self.config.get("auditor_interval_seconds", 300)),
                    int(self.config.get("auditor_timeout_seconds", 2100)),
                ),
            ),
            threading.Thread(
                name="tine-plugin-publisher",
                target=self.worker,
                args=(
                    "publisher",
                    [python, str(self.root / "publisher_daemon.py"), "--once", "--config", self.config["publisher_config"]],
                    int(self.config.get("publisher_interval_seconds", 120)),
                    int(self.config.get("publisher_timeout_seconds", 300)),
                ),
            ),
        ]
        self.save()
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    state_root = pathlib.Path(config["state_root"]).expanduser()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root.chmod(0o700)
    lock_path = state_root / "supervisor.lock"
    lock_file = lock_path.open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another auditor supervisor already holds the lock") from None
    supervisor = Supervisor(config)
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stop.set())
    signal.signal(signal.SIGINT, lambda *_: supervisor.stop.set())
    supervisor.run()


if __name__ == "__main__":
    main()
