from __future__ import annotations

import ast
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class HostDependencyIssue:
    code: str
    message: str
    path: str = ""


def load_host_import_registry(path: Path) -> dict[str, dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("modules"), dict):
        raise ValueError(f"invalid host import registry: {path}")
    return dict(value["modules"])


def _imports(path: Path) -> tuple[set[str], list[HostDependencyIssue]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return set(), [HostDependencyIssue("HOST_SOURCE_INVALID", f"{type(exc).__name__}: {exc}", str(path))]
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".", 1)[0])
    return modules, []


def _local_module(module: str, roots: Iterable[Path]) -> Path | None:
    for root in roots:
        file_candidate = root / f"{module}.py"
        package_candidate = root / module / "__init__.py"
        if file_candidate.is_file():
            return file_candidate
        if package_candidate.is_file():
            return package_candidate
    return None


def host_entry_files(mls_root: Path, task_id: str, asset_manifest_dir: Path) -> list[Path]:
    task_root = mls_root / "tasks" / task_id
    entries = [task_root / "parser.py", task_root / "score_spec.py"]
    config_path = task_root / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for baseline in config.get("baselines", {}).values():
            if isinstance(baseline, dict) and baseline.get("edit_ops"):
                entries.append(task_root / str(baseline["edit_ops"]))
    asset_path = asset_manifest_dir / f"{task_id}.json"
    if asset_path.is_file():
        asset_manifest = json.loads(asset_path.read_text(encoding="utf-8"))
        for item in asset_manifest.get("assets", []):
            destination = mls_root / str(item["destination"])
            source = mls_root / str(item.get("source", ""))
            entries.append(destination if destination.is_file() else source)
    return [path.resolve() for path in entries if path.is_file()]


def audit_host_dependencies(
    mls_root: Path,
    task_id: str,
    asset_manifest_dir: Path,
    registry_path: Path,
) -> dict[str, object]:
    """Recursively follow host-side Python imports and require an approved mapping for missing modules."""
    registry = load_host_import_registry(registry_path)
    task_root = (mls_root / "tasks" / task_id).resolve()
    local_roots = [task_root, (mls_root / "holdout" / task_id).resolve()]
    pending = host_entry_files(mls_root, task_id, asset_manifest_dir)
    visited: set[Path] = set()
    third_party: set[str] = set()
    issues: list[HostDependencyIssue] = []
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        modules, parse_issues = _imports(path)
        issues.extend(parse_issues)
        for module in modules:
            if module in sys.stdlib_module_names:
                continue
            local = _local_module(module, [path.parent, *local_roots])
            if local is not None:
                pending.append(local.resolve())
                continue
            third_party.add(module)

    missing: list[str] = []
    for module in sorted(third_party):
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if available:
            continue
        entry = registry.get(module)
        approved_tasks = set(entry.get("tasks", [])) if entry else set()
        if entry and ("*" in approved_tasks or task_id in approved_tasks):
            missing.append(module)
            issues.append(HostDependencyIssue(
                "HOST_REQUIREMENT_MISSING",
                f"approved host module {module!r} is not importable; install {entry['requirement']}",
            ))
        else:
            issues.append(HostDependencyIssue(
                "HOST_IMPORT_UNMAPPED",
                f"host import {module!r} is neither importable nor mapped to an approved requirement",
            ))
    return {
        "task": task_id,
        "files": [str(path) for path in sorted(visited)],
        "third_party_imports": sorted(third_party),
        "missing": missing,
        "issues": [issue.__dict__ for issue in issues],
        "ready": not issues,
    }
