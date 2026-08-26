from __future__ import annotations

import json
import hashlib
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .io import atomic_write_json, read_json
from .manifest import RoundSpec


def packages_for_round(mls_root: Path, round_spec: RoundSpec) -> list[str]:
    packages: set[str] = set()
    for task in round_spec.tasks:
        path = mls_root / "tasks" / task.id / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        packages.update(item["package"] for item in config.get("test_cmds", []) if item.get("package"))
    return sorted(packages)


def prepare_commands(python: Path, mls_root: Path, config: Path, packages: list[str]) -> list[list[str]]:
    return [
        [str(python), "-m", "mlsbench.cli", "build", package, "--pull", "--config", str(config)]
        for package in packages
    ]


@contextmanager
def _exclusive_file_lock(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if __import__("os").name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute_commands(commands: list[list[str]], cwd: Path, lock_path: Path | None = None) -> None:
    with _exclusive_file_lock(lock_path):
        for command in commands:
            subprocess.run(command, cwd=cwd, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_images(packages: list[str], artifact_dir: Path) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    images: list[dict[str, str]] = []
    for package in packages:
        image = f"mlsbench/{package.lower()}:latest"
        destination = artifact_dir / f"{package.lower()}.tar"
        temporary = destination.with_suffix(".tar.part")
        if temporary.exists():
            temporary.unlink()
        subprocess.run(["docker", "save", "--output", str(temporary), image], check=True)
        temporary.replace(destination)
        images.append({"package": package, "image": image, "file": destination.name, "sha256": _sha256(destination)})
    manifest = artifact_dir / "images.json"
    atomic_write_json(manifest, {"schema": 1, "images": images})
    return manifest


def hydrate_images(artifact_dir: Path) -> list[str]:
    manifest = read_json(artifact_dir / "images.json")
    loaded: list[str] = []
    for item in manifest["images"]:
        archive = artifact_dir / item["file"]
        actual = _sha256(archive)
        if actual != item["sha256"]:
            raise ValueError(f"image archive SHA256 mismatch: {archive}")
        subprocess.run(["docker", "load", "--input", str(archive)], check=True)
        loaded.append(item["image"])
    return loaded


def cleanup_images(artifact_dir: Path, *, delete_artifacts: bool) -> list[str]:
    manifest_path = artifact_dir / "images.json"
    manifest = read_json(manifest_path)
    removed: list[str] = []
    for item in manifest["images"]:
        subprocess.run(["docker", "image", "rm", item["image"]], check=True)
        removed.append(item["image"])
    if delete_artifacts:
        for item in manifest["images"]:
            archive = artifact_dir / item["file"]
            if archive.parent.resolve() != artifact_dir.resolve():
                raise ValueError(f"artifact path escapes exact round directory: {archive}")
            archive.unlink()
        manifest_path.unlink()
        try:
            artifact_dir.rmdir()
        except OSError:
            pass
    return removed
