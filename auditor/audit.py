#!/usr/bin/env python3
from __future__ import annotations
import argparse
import base64
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from codex_review import review


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def safe_git(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8",
    }
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *command], check=True, text=True, env=env, **kwargs)


def podman_base(
    image: str,
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    cache: pathlib.Path,
    output: pathlib.Path,
    network: str,
) -> list[str]:
    workdir = pathlib.PurePosixPath("/src") / plugin_root.relative_to(source).as_posix()
    return [
        "podman", "--cgroup-manager=cgroupfs", "run", "--rm", f"--network={network}",
        "--cap-drop=all",
        "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=1g", "--cpus=2",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "-v", f"{source}:/src:ro,Z", "-v", f"{cache}:/cache:rw,Z", "-v", f"{output}:/out:rw,Z",
        "--workdir", str(workdir),
        image,
    ]


def audit(submission_path: pathlib.Path, tine: pathlib.Path, image: str, max_source: int) -> dict:
    submission = json.loads(submission_path.read_text())
    with tempfile.TemporaryDirectory(prefix="tine-plugin-audit-") as temp:
        temp_path = pathlib.Path(temp)
        source, cache, output = temp_path / "source", temp_path / "cache", temp_path / "output"
        cache.mkdir(); output.mkdir()
        safe_git(["clone", "--filter=blob:none", "--no-checkout", submission["repository"], str(source)])
        safe_git(["-C", str(source), "fetch", "--depth=1", "origin", submission["commit"]])
        safe_git(["-C", str(source), "checkout", "--detach", submission["commit"]])
        actual = safe_git(["-C", str(source), "rev-parse", "HEAD"], capture_output=True).stdout.strip()
        if actual != submission["commit"]:
            raise RuntimeError("checkout did not resolve to the submitted commit")
        manifest_path = (source / submission["manifestPath"]).resolve()
        if source.resolve() not in manifest_path.parents:
            raise RuntimeError("manifest path escaped checkout")
        plugin_root = manifest_path.parent
        fetch = podman_base(image, source, plugin_root, cache, output, "slirp4netns")
        run([*fetch, "fetch"], timeout=600)
        build = podman_base(image, source, plugin_root, cache, output, "none")
        run([*build, "build"], timeout=600)
        manifest = json.loads(manifest_path.read_text())
        expected = pathlib.Path(manifest["entry"]).name
        artifact = output / "artifacts" / expected
        if not artifact.is_file():
            raise RuntimeError(f"build did not produce declared entry {expected}")
        entry = plugin_root / manifest["entry"]
        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact, entry)
        checker_path = temp_path / "check.json"
        completed = subprocess.run(
            ["node", str(tine / "scripts/tine-plugin.mjs"), "check", str(plugin_root), "--json"],
            capture_output=True, text=True, check=False,
        )
        if not completed.stdout.strip():
            raise RuntimeError(f"plugin checker produced no report: {completed.stderr[:500]}")
        checker_path.write_text(completed.stdout)
        checker = json.loads(completed.stdout)
        ai = review(plugin_root, checker, pathlib.Path(__file__).parents[1] / "schemas" / "audit.schema.json", max_source)
        deterministic_pass = checker.get("status") == "passed"
        low_risk = checker.get("risk") == "low"
        if not deterministic_pass:
            disposition = "quarantine"
        elif ai["disposition"] == "reject":
            disposition = "reject"
        elif low_risk and ai["disposition"] == "pass" and not ai["uncertain"]:
            disposition = "publish"
        else:
            disposition = "quarantine"
        return {
            "format": "tine-plugin-audit-result/v1",
            "submission": submission,
            "commitVerified": actual,
            "checker": checker,
            "aiReview": ai,
            "disposition": disposition,
            "package": {
                "manifest": manifest,
                "wasmBase64": base64.b64encode(artifact.read_bytes()).decode("ascii"),
                "sha256": checker["wasm"]["sha256"],
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission", type=pathlib.Path)
    parser.add_argument("--tine", type=pathlib.Path, required=True)
    parser.add_argument("--image", default="localhost/tine-plugin-builder:0.1")
    parser.add_argument("--max-source-bytes", type=int, default=524288)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = audit(args.submission, args.tine, args.image, args.max_source_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    os.replace(temp, args.output)


if __name__ == "__main__":
    main()
