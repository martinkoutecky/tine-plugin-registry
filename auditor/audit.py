#!/usr/bin/env python3
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import tomllib
from codex_review import review
from validation import (
    submission_kind, theme_review_publishable, validate_manifest_identity, validate_source_tree,
    validate_submission,
)


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


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


def bwrap_base(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    cache: pathlib.Path,
    output: pathlib.Path,
    network: bool,
) -> list[str]:
    """Minimal credential-free root for hosts where rootless Podman is absent."""
    workdir = pathlib.PurePosixPath("/src") / plugin_root.relative_to(source).as_posix()
    command = [
        "bwrap", "--die-with-parent", "--new-session", "--unshare-user-try", "--cap-drop", "ALL",
        "--clearenv", "--setenv", "PATH", "/opt/cargo/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/home", "--setenv", "RUSTUP_HOME", "/opt/rustup",
        "--setenv", "LANG", "C.UTF-8",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--dir", "/opt", "--ro-bind", "/opt/cargo", "/opt/cargo",
        "--ro-bind", "/opt/rustup", "/opt/rustup",
        "--dir", "/etc", "--ro-bind", "/etc/ssl", "/etc/ssl",
        "--ro-bind", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/hosts", "/etc/hosts",
        "--ro-bind", "/proc", "/proc", "--dev", "/dev",
        "--dir", "/home", "--dir", "/home/koutecky", "--tmpfs", "/tmp",
        "--ro-bind", str(source), "/src", "--bind", str(cache), "/cache",
        "--bind", str(output), "/out",
        "--ro-bind", str(pathlib.Path(__file__).with_name("container") / "build.sh"), "/builder.sh",
        "--chdir", str(workdir),
    ]
    if not network:
        command.append("--unshare-net")
    return [*command, "/bin/bash", "/builder.sh"]


def locked_sdk_source(plugin_root: pathlib.Path, temp: pathlib.Path, manifest: dict) -> list[tuple[str, pathlib.Path]]:
    lock_path = plugin_root / "Cargo.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        return []
    lock = tomllib.loads(lock_path.read_text())
    package = next((item for item in lock.get("package", []) if item.get("name") == "tine-plugin-sdk"), None)
    source = package.get("source", "") if package else ""
    prefix = "git+https://github.com/martinkoutecky/tine?"
    if not source.startswith(prefix) or "#" not in source:
        return []
    commit = source.rsplit("#", 1)[1]
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        return []
    checkout = temp / "sdk-source"
    safe_git(["clone", "--filter=blob:none", "--no-checkout", "https://github.com/martinkoutecky/tine", str(checkout)])
    safe_git(["-C", str(checkout), "fetch", "--depth=1", "origin", commit])
    safe_git(["-C", str(checkout), "checkout", "--detach", commit])
    sdk = checkout / "plugin-sdk" / "rust"
    sources = [(f"locked-tine-plugin-sdk@{commit}", sdk)] if sdk.is_dir() else []
    if manifest.get("contributions", {}).get("blockDecorations"):
        evidence = temp / "host-decoration-evidence"
        for relative in [
            "src/plugins/manager.ts", "src/plugins/manifest.ts",
            "src/components/Block.tsx", "src/styles/app.css",
            # Setting-to-style boundary (plugin API 0.3): validation and row styling.
            "src/components/block/rowChrome.tsx", "src/plugins/settings.ts",
            "src/plugins/color.mjs",
            # Guest runtime boundary: allocation/ownership and message limits.
            "src/plugins/runtime.ts", "src/plugins/runtime.worker.ts", "src/plugins/guest.ts",
            "src/plugins/protocol.ts",
        ]:
            source = checkout / relative
            if not source.is_file():
                continue  # older locked revisions predate some of these files
            destination = evidence / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        sources.append((f"locked-tine-host-decoration@{commit}", evidence))
    return sources


PORT_EVIDENCE_LIMIT = 400_000
PORT_EVIDENCE_MAX_FILES = 200


# Narrowing a large theme upstream reads at most this much to find the defining files.
PORT_EVIDENCE_SCAN_MAX_FILES = 3000
PORT_EVIDENCE_SCAN_LIMIT = 30_000_000


def port_evidence_is_metadata(path: pathlib.PurePath) -> bool:
    name = path.name.lower()
    stem = name.split(".", 1)[0]
    return stem in {"license", "licence", "copying", "notice"} or name in {"manifest.json", "package.json"} or name.startswith("readme")


def port_evidence_path_relevant(path: pathlib.Path, kind: str) -> bool:
    """Keep theme evidence focused on authored styling, not whole application bundles."""
    name = path.name.lower()
    if port_evidence_is_metadata(path):
        return True
    if kind == "theme":
        if ".bundle." in name or ".min." in name:
            return False
        return path.suffix.lower() in {".css", ".scss", ".sass", ".less"}
    return path.suffix.lower() in {".css", ".scss", ".js", ".ts", ".tsx"}


def theme_custom_properties(manifest: object) -> set[str]:
    """Every CSS custom property name (`--x`) the theme manifest sets, at any depth."""
    names: set[str] = set()
    stack = [manifest]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            names.update(key for key in value if isinstance(key, str) and key.startswith("--"))
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return names


CUSTOM_PROPERTY_DECLARATION = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")


def defines_custom_property(text: str, names: set[str]) -> bool:
    return any(match.group(1) in names for match in CUSTOM_PROPERTY_DECLARATION.finditer(text))


def narrow_theme_port_evidence(
    selected: list[tuple[str, str]], names: set[str], read_blob, prefetch,
) -> list[tuple[str, str]] | None:
    """For an upstream too large to include whole: its style files that define a custom
    property the theme sets, plus top-level license/readme metadata. None if that is
    still over the review limit, or nothing matches."""
    if not names:
        return None
    style = [(rel, blob) for rel, blob in selected if not port_evidence_is_metadata(pathlib.PurePosixPath(rel))]
    if len(style) > PORT_EVIDENCE_SCAN_MAX_FILES:
        return None
    prefetch([blob for _, blob in style])
    kept: list[tuple[str, str]] = []
    scanned = 0
    for rel, blob in sorted(style):
        data = read_blob(blob)
        scanned += len(data.encode())
        if scanned > PORT_EVIDENCE_SCAN_LIMIT:
            return None
        if defines_custom_property(data, names):
            kept.append((rel, data))
    if not kept:
        return None
    top_metadata = sorted(
        (rel, blob) for rel, blob in selected
        if "/" not in rel and port_evidence_is_metadata(pathlib.PurePosixPath(rel))
    )
    prefetch([blob for _, blob in top_metadata])
    kept += [(rel, read_blob(blob)) for rel, blob in top_metadata]
    if len(kept) > PORT_EVIDENCE_MAX_FILES or sum(len(data.encode()) for _, data in kept) > PORT_EVIDENCE_LIMIT:
        return None
    return kept


def pinned_port_source(manifest: dict, temp: pathlib.Path, kind: str) -> list[tuple[str, pathlib.Path]]:
    """Fetch declared port evidence as inert review input; never build or execute it."""
    port = manifest.get("portedFrom")
    if not isinstance(port, dict):
        return []
    source, revision = port.get("source"), port.get("revision")
    if (
        not isinstance(source, str)
        or not source.startswith("https://github.com/")
        or not isinstance(revision, str)
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        return []
    # Fetch only the declared revision's tree, then only the relevant blobs: a
    # full-history clone of a large upstream (microsoft/vscode) crashed audits.
    checkout = temp / "ported-source"
    safe_git(["init", "-q", str(checkout)])
    safe_git(["-C", str(checkout), "remote", "add", "origin", source])
    safe_git(["-C", str(checkout), "fetch", "-q", "--depth=1", "--filter=blob:none", "origin", revision], timeout=300)
    actual = safe_git(["-C", str(checkout), "rev-parse", "FETCH_HEAD"], capture_output=True).stdout.strip()
    if actual != revision:
        raise RuntimeError("ported source did not resolve to the declared revision")
    listing = safe_git(
        ["-C", str(checkout), "ls-tree", "-r", "--full-tree", "-z", revision],
        capture_output=True, timeout=300,
    ).stdout
    # No `-l`: object sizes would lazily fetch every blob of the partial clone.
    selected: list[tuple[str, str]] = []
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, rel = entry.partition("\t")
        mode, kind_, blob = meta.split()
        # Regular files only: no symlinks (120000) or submodules (160000).
        if kind_ != "blob" or mode not in {"100644", "100755"}:
            continue
        rel_path = pathlib.PurePosixPath(rel)
        if ".." in rel_path.parts or not port_evidence_path_relevant(pathlib.Path(rel), kind):
            continue
        selected.append((rel, blob))
    evidence = temp / "ported-evidence"
    evidence.mkdir()
    label = f"declared-ported-source@{revision}"

    def read_blob(blob: str) -> str:
        return safe_git(
            ["-C", str(checkout), "cat-file", "blob", blob],
            capture_output=True, timeout=120, errors="replace",
        ).stdout

    def prefetch(blobs: list[str]) -> None:
        # One request per chunk instead of a lazy fetch per blob.
        for start in range(0, len(blobs), 500):
            safe_git(
                ["-C", str(checkout), "fetch", "-q", "--no-tags", "--no-write-fetch-head",
                 "--filter=blob:none", "origin", *blobs[start:start + 500]],
                timeout=300,
            )

    def write(files: list[tuple[str, str]]) -> None:
        for rel, data in files:
            destination = evidence / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(data)

    def omitted(reason: str) -> list[tuple[str, pathlib.Path]]:
        # A theme is inert colour data: an oversized upstream is reported to the
        # reviewer as unavailable evidence rather than failing the audit closed.
        (evidence / "PORT-EVIDENCE-OMITTED.txt").write_text(
            f"The declared upstream {source}@{revision} {reason}, over the review limit; "
            "its sources are not included.\n"
        )
        return [(label, evidence)]

    files: list[tuple[str, str]] = []
    if len(selected) > PORT_EVIDENCE_MAX_FILES:
        too_big = f"has {len(selected)} relevant files"
    else:
        too_big = ""
        prefetch([blob for _, blob in selected])
        copied_bytes = 0
        for rel, blob in sorted(selected):
            data = read_blob(blob)
            copied_bytes += len(data.encode())
            if copied_bytes > PORT_EVIDENCE_LIMIT:
                too_big = f"has more than {PORT_EVIDENCE_LIMIT} bytes of relevant files"
                break
            files.append((rel, data))
    if not too_big:
        write(files)
        return [(label, evidence)]
    if kind != "theme":
        raise RuntimeError("declared port evidence exceeds the review limit")
    narrowed = narrow_theme_port_evidence(selected, theme_custom_properties(manifest), read_blob, prefetch)
    if narrowed is None:
        return omitted(too_big)
    write(narrowed)
    (evidence / "PORT-EVIDENCE-NARROWED.txt").write_text(
        f"The declared upstream {source}@{revision} {too_big}, over the review limit. Included are "
        f"only its {len(narrowed)} files that define a CSS custom property this theme sets, or are "
        "top-level license/readme metadata; the rest of the upstream is not included.\n"
    )
    return [(label, evidence)]


def audit(submission_path: pathlib.Path, tine: pathlib.Path, image: str, max_source: int) -> dict:
    submission = validate_submission(json.loads(submission_path.read_text()))
    with tempfile.TemporaryDirectory(prefix="tine-plugin-audit-") as temp:
        temp_path = pathlib.Path(temp)
        source, cache, output = temp_path / "source", temp_path / "cache", temp_path / "output"
        cache.mkdir(); output.mkdir()
        safe_git(["clone", "--filter=blob:none", "--no-checkout", submission["repository"], str(source)], timeout=300)
        safe_git(["-C", str(source), "fetch", "--depth=1", "origin", submission["commit"]], timeout=300)
        safe_git(["-C", str(source), "checkout", "--detach", submission["commit"]], timeout=300)
        actual = safe_git(["-C", str(source), "rev-parse", "HEAD"], capture_output=True).stdout.strip()
        if actual != submission["commit"]:
            raise RuntimeError("checkout did not resolve to the submitted commit")
        validate_source_tree(source)
        manifest_path = (source / submission["manifestPath"]).resolve()
        if source.resolve() not in manifest_path.parents:
            raise RuntimeError("manifest path escaped checkout")
        if not manifest_path.is_file():
            raise RuntimeError("manifest path is not a regular file")
        plugin_root = manifest_path.parent
        manifest = validate_manifest_identity(json.loads(manifest_path.read_text()), submission)
        kind = submission_kind(submission)
        if kind == "theme":
            completed = subprocess.run(
                ["node", str(tine / "scripts/tine-theme.mjs"), "check", str(plugin_root), "--json"],
                capture_output=True, text=True, check=False,
            )
            if not completed.stdout.strip():
                raise RuntimeError(f"theme checker produced no report: {completed.stderr[:500]}")
            checker = json.loads(completed.stdout)
            checker["risk"] = "low"
            checker["provenance"] = {
                "sourceCommit": actual,
                "manifestLoadedFromThatCheckout": True,
                "dependencyFetchHadNoCredentials": True,
                "buildNetwork": "not-applicable",
                "manifestSha256": checker.get("sha256"),
            }
            port_sources = pinned_port_source(manifest, temp_path, "theme")
            ai = review(
                plugin_root, checker,
                pathlib.Path(__file__).parents[1] / "schemas" / "audit.schema.json",
                max_source, extra_sources=port_sources, kind="theme",
            )
            deterministic_pass = checker.get("status") == "passed"
            if not deterministic_pass:
                disposition = "quarantine"
            elif ai["disposition"] == "reject":
                disposition = "reject"
            elif theme_review_publishable(ai):
                disposition = "publish"
            else:
                disposition = "quarantine"
            return {
                "format": "tine-package-audit-result/v1",
                "submission": submission,
                "commitVerified": actual,
                "checker": checker,
                "aiReview": ai,
                "disposition": disposition,
                "package": {
                    "manifest": manifest,
                    "manifestSha256": hashlib.sha256(canonical(manifest)).hexdigest(),
                },
            }
        entry_rel = manifest.get("entry")
        if not isinstance(entry_rel, str):
            raise RuntimeError("plugin manifest entry is invalid")
        entry = (plugin_root / entry_rel).resolve()
        if plugin_root.resolve() not in entry.parents:
            raise RuntimeError("plugin entry escaped its source root")
        use_podman = shutil.which("podman") is not None
        if not use_podman and shutil.which("bwrap") is None:
            raise RuntimeError("neither rootless Podman nor Bubblewrap is available")
        fetch = (podman_base(image, source, plugin_root, cache, output, "slirp4netns")
                 if use_podman else bwrap_base(source, plugin_root, cache, output, True))
        run([*fetch, "fetch"], timeout=600)
        build = (podman_base(image, source, plugin_root, cache, output, "none")
                 if use_podman else bwrap_base(source, plugin_root, cache, output, False))
        run([*build, "build"], timeout=600)
        expected = pathlib.Path(manifest["entry"]).name
        artifact = output / "artifacts" / expected
        if not artifact.is_file():
            raise RuntimeError(f"build did not produce declared entry {expected}")
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
        checker["provenance"] = {
            "sourceCommit": actual,
            "artifactBuiltFromThatCheckout": True,
            "dependencyFetchHadNoCredentials": True,
            "buildNetwork": "none",
            "artifactSha256": checker.get("wasm", {}).get("sha256"),
        }
        extra_sources = [
            *locked_sdk_source(plugin_root, temp_path, manifest),
            *pinned_port_source(manifest, temp_path, "plugin"),
        ]
        ai = review(
            plugin_root,
            checker,
            pathlib.Path(__file__).parents[1] / "schemas" / "audit.schema.json",
            max_source,
            extra_sources,
            kind="plugin",
        )
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
                "manifestSha256": hashlib.sha256(canonical(manifest)).hexdigest(),
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
