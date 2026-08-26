from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit(mls_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(mls_root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _inside(root: Path, relative: str) -> Path:
    destination = (root / relative).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes allowed root: {relative}") from exc
    return destination


def prepare_task_assets(
    task_id: str,
    *,
    asset_manifest_dir: Path,
    source_root: Path | None,
    mls_root: Path,
    receipt_root: Path,
    execute: bool,
) -> tuple[bool, list[str]]:
    """Verify or materialize registered task assets without overwriting different data."""
    manifest_path = asset_manifest_dir / f"{task_id}.json"
    if not manifest_path.is_file():
        return True, [f"{task_id}: no registered external assets"]
    manifest: dict[str, Any] = read_json(manifest_path)
    if manifest.get("task") != task_id:
        return False, [f"{task_id}: asset manifest names {manifest.get('task')!r}"]
    current_commit = _commit(mls_root)
    compatible = set(manifest.get("compatible_mls_commits", []))
    if compatible and current_commit not in compatible:
        return False, [f"{task_id}: MLS commit {current_commit} is not approved by asset manifest"]

    ready = True
    actions: list[str] = []
    receipt_assets: list[dict[str, Any]] = []
    previous_receipt_path = receipt_root / f"{task_id}.json"
    previous_created = {}
    if previous_receipt_path.is_file():
        previous = read_json(previous_receipt_path)
        previous_created = {str(item["destination"]): bool(item.get("created")) for item in previous.get("assets", [])}
    for asset in manifest.get("assets", []):
        relative_destination = str(asset["destination"])
        destination = _inside(mls_root, relative_destination)
        expected = str(asset["sha256"]).lower()
        if destination.is_file():
            actual = sha256(destination)
            if actual != expected:
                ready = False
                actions.append(f"BLOCKED {relative_destination}: existing SHA256 {actual}, expected {expected}")
                continue
            actions.append(f"READY {relative_destination}: already present and verified")
            receipt_assets.append({
                "destination": relative_destination,
                "sha256": actual,
                "created": previous_created.get(relative_destination, False),
            })
            continue
        if source_root is None:
            ready = False
            actions.append(f"BLOCKED {relative_destination}: missing and no private asset source root was supplied")
            continue
        source = _inside(source_root, str(asset["source"]))
        if not source.is_file():
            ready = False
            actions.append(f"BLOCKED {relative_destination}: source is missing at {source}")
            continue
        actual = sha256(source)
        if actual != expected:
            ready = False
            actions.append(f"BLOCKED {relative_destination}: source SHA256 {actual}, expected {expected}")
            continue
        actions.append(("APPLY " if execute else "WOULD_APPLY ") + f"{source} -> {destination} [{actual}]")
        if execute:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source, temporary)
                if sha256(temporary) != expected:
                    raise ValueError(f"copied asset failed SHA256 verification: {temporary}")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        receipt_assets.append({"destination": relative_destination, "sha256": actual, "created": execute})

    if execute and ready:
        atomic_write_json(
            receipt_root / f"{task_id}.json",
            {
                "schema": 1,
                "task": task_id,
                "mls_commit": current_commit,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "manifest_sha256": sha256(manifest_path),
                "assets": receipt_assets,
            },
        )
    return ready, actions


def unload_task_assets(task_id: str, *, mls_root: Path, receipt_root: Path, execute: bool) -> tuple[bool, list[str]]:
    """Remove only assets this loader created and whose content is unchanged."""
    receipt_path = receipt_root / f"{task_id}.json"
    if not receipt_path.is_file():
        return False, [f"{task_id}: no asset receipt exists"]
    receipt = read_json(receipt_path)
    actions: list[str] = []
    safe = True
    for asset in receipt.get("assets", []):
        if not asset.get("created"):
            continue
        destination = _inside(mls_root, str(asset["destination"]))
        if not destination.exists():
            actions.append(f"ABSENT {destination}")
            continue
        actual = sha256(destination)
        if actual != asset["sha256"]:
            safe = False
            actions.append(f"REFUSE {destination}: content changed to {actual}")
            continue
        actions.append(("REMOVE " if execute else "WOULD_REMOVE ") + str(destination))
        if execute:
            destination.unlink()
    if execute and safe:
        receipt_path.unlink()
    return safe, actions
