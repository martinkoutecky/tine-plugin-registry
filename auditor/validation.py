"""Small, dependency-free trust-boundary validators shared by auditor/publisher."""

from __future__ import annotations
import pathlib
import re

ID = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{1,62}[a-z0-9])$")
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
REPOSITORY = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SUBMISSION_FIELDS = {"schemaVersion", "pluginId", "version", "repository", "commit", "manifestPath"}


def validate_submission(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != SUBMISSION_FIELDS:
        raise ValueError("submission fields are invalid")
    if value.get("schemaVersion") != 1:
        raise ValueError("submission schemaVersion must be 1")
    if not isinstance(value.get("pluginId"), str) or not ID.fullmatch(value["pluginId"]):
        raise ValueError("submission pluginId is invalid")
    if not isinstance(value.get("version"), str) or not VERSION.fullmatch(value["version"]):
        raise ValueError("submission version is invalid")
    if not isinstance(value.get("repository"), str) or not REPOSITORY.fullmatch(value["repository"]):
        raise ValueError("submission repository is invalid")
    if not isinstance(value.get("commit"), str) or not COMMIT.fullmatch(value["commit"]):
        raise ValueError("submission commit is invalid")
    manifest = value.get("manifestPath")
    if (
        not isinstance(manifest, str)
        or manifest.startswith("/")
        or ".." in pathlib.PurePosixPath(manifest).parts
        or not manifest.endswith("manifest.json")
    ):
        raise ValueError("submission manifestPath is invalid")
    return value


def validate_manifest_identity(manifest: object, submission: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("plugin manifest is not an object")
    if manifest.get("id") != submission["pluginId"] or manifest.get("version") != submission["version"]:
        raise ValueError("manifest identity does not match the immutable submission")
    return manifest


def validate_source_tree(root: pathlib.Path, max_bytes: int = 4 * 1024 * 1024, max_files: int = 5_000) -> None:
    """Reject links and oversized trees before any host-side source reads."""
    total = 0
    files = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        metadata = path.lstat()
        if path.is_symlink():
            raise ValueError(f"source symlinks are not accepted: {rel.as_posix()}")
        if not path.is_file():
            continue
        files += 1
        total += metadata.st_size
        if files > max_files or total > max_bytes:
            raise ValueError("plugin source tree exceeds the registry intake limit")
