from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, atomic_write_text, read_json
from .manifest import Manifest
from mls_agent.selection import select_agent_image_package


@dataclass(frozen=True)
class AuditIssue:
    level: str
    code: str
    message: str
    path: str = ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(mls_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(mls_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _asset_manifest(asset_manifest_dir: Path, task_id: str) -> dict[str, Any]:
    path = asset_manifest_dir / f"{task_id}.json"
    return read_json(path) if path.is_file() else {"task": task_id, "assets": []}


def _package_registry(mls_root: Path) -> dict[str, dict[str, str]]:
    """Parse the simple pinned package registry without adding a YAML dependency."""
    path = mls_root / "vendor" / "packages.yaml"
    if not path.is_file():
        return {}
    registry: dict[str, dict[str, str]] = {}
    current = ""
    in_packages = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip() == "packages:":
            in_packages = True
            continue
        if not in_packages or not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        package_match = re.match(r"^  ([^:#][^:]*):\s*$", raw_line)
        if package_match:
            current = package_match.group(1).strip()
            registry[current] = {}
            continue
        field_match = re.match(r"^    (url|commit):\s*(.*?)\s*$", raw_line)
        if current and field_match:
            registry[current][field_match.group(1)] = field_match.group(2).strip("'\"")
    return registry


def _check_python(path: Path, task_id: str) -> tuple[set[str], list[AuditIssue]]:
    issues: list[AuditIssue] = []
    imports: set[str] = set()
    task_root = path.parent
    project_root = task_root.parent.parent
    display_path = str(path.relative_to(project_root))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return imports, [AuditIssue("BLOCKED", "PYTHON_PARSE_ERROR", str(exc), display_path)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".", 1)[0])
    for module in sorted(imports):
        if module in sys.stdlib_module_names or module == "mlsbench":
            continue
        local_candidates = (
            task_root / f"{module}.py",
            project_root / "holdout" / task_id / f"{module}.py",
            task_root / "holdout" / task_id / f"{module}.py",
        )
        if any(candidate.is_file() for candidate in local_candidates):
            continue
        available = importlib.util.find_spec(module) is not None
        level = "INFO" if available else "WARN"
        code = "HOST_IMPORT_AVAILABLE" if available else "HOST_IMPORT_UNVERIFIED"
        issues.append(AuditIssue(level, code, f"host import {module!r} must exist in the platform venv", display_path))
    return imports, issues


def _validate_relative_file(task_root: Path, relative: str, code: str) -> AuditIssue | None:
    destination = (task_root / relative).resolve()
    try:
        destination.relative_to(task_root.resolve())
    except ValueError:
        return AuditIssue("BLOCKED", "PATH_ESCAPE", f"task path escapes task root: {relative}", relative)
    if not destination.is_file():
        return AuditIssue("BLOCKED", code, f"referenced task file is missing: {relative}", relative)
    return None


def audit_task(mls_root: Path, task_id: str, asset_manifest_dir: Path) -> dict[str, Any]:
    task_root = mls_root / "tasks" / task_id
    issues: list[AuditIssue] = []
    required = ("config.json", "parser.py", "score_spec.py", "task_description.md")
    for relative in required:
        if not (task_root / relative).is_file():
            issues.append(AuditIssue("BLOCKED", "REQUIRED_FILE_MISSING", f"missing {relative}", relative))
    config: dict[str, Any] = {}
    config_path = task_root / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(AuditIssue("BLOCKED", "CONFIG_INVALID", str(exc), "config.json"))

    imports: set[str] = set()
    for relative in ("parser.py", "score_spec.py"):
        path = task_root / relative
        if path.is_file():
            found, python_issues = _check_python(path, task_id)
            imports.update(found)
            issues.extend(python_issues)

    packages: set[str] = set()
    registry = _package_registry(mls_root)
    registry_by_normalized = {
        name.lower().replace("-", "").replace("_", ""): (name, value)
        for name, value in registry.items()
    }
    commands: list[dict[str, Any]] = []
    for entry in config.get("test_cmds", []):
        if not isinstance(entry, dict):
            issues.append(AuditIssue("BLOCKED", "TEST_COMMAND_INVALID", "test_cmds entry is not an object", "config.json"))
            continue
        commands.append(entry)
        package = entry.get("package")
        if package:
            packages.add(str(package))
        command = entry.get("cmd")
        if not isinstance(command, str) or not command.strip():
            issues.append(AuditIssue("BLOCKED", "TEST_COMMAND_INVALID", "test command is empty", "config.json"))
            continue
        try:
            first = shlex.split(command)[0]
        except (ValueError, IndexError) as exc:
            issues.append(AuditIssue("BLOCKED", "TEST_COMMAND_INVALID", str(exc), "config.json"))
            continue
        if "/" in first and not first.startswith(("/", "./vendor/")) and "{" not in first:
            issue = _validate_relative_file(task_root, first.removeprefix("./"), "TEST_SCRIPT_MISSING")
            if issue:
                issues.append(issue)

    for package in sorted(packages):
        package_config = mls_root / "vendor" / "pkg_configs" / package / "config.json"
        if not package_config.is_file():
            issues.append(AuditIssue("BLOCKED", "PACKAGE_CONFIG_MISSING", f"missing package config for {package}", str(package_config.relative_to(mls_root))))
            continue
        try:
            package_value = json.loads(package_config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(AuditIssue("BLOCKED", "PACKAGE_CONFIG_INVALID", str(exc), str(package_config.relative_to(mls_root))))
            continue
        for dependency in package_value.get("data_deps", []):
            if isinstance(dependency, dict):
                host_path = str(dependency.get("host_path", ""))
                replacements = {
                    "{data_root}": str(mls_root / "vendor" / "data"),
                    "{project_root}": str(mls_root),
                }
                def expand(value: str) -> Path:
                    for token, replacement in replacements.items():
                        value = value.replace(token, replacement)
                    return Path(value)
                ready_files = [expand(str(value)) for value in dependency.get("ready_files", [])]
                expanded_host = expand(host_path) if host_path else None
                ready = all(path.is_file() for path in ready_files) if ready_files else bool(
                    expanded_host and expanded_host.exists() and (not expanded_host.is_dir() or any(expanded_host.iterdir()))
                )
                if ready:
                    issues.append(AuditIssue("INFO", "DATA_READY", f"package {package} data is already present: {host_path or '(dynamic)'}", str(package_config.relative_to(mls_root))))
                    continue
                prepare = str(dependency.get("prepare", ""))
                prepare_path = mls_root / prepare if prepare else None
                if prepare_path and prepare_path.is_file():
                    issues.append(AuditIssue("WARN", "DATA_PREP_REQUIRED", f"official prepare script will create {host_path or '(dynamic)'}", prepare))
                else:
                    issues.append(AuditIssue("BLOCKED", "DATA_SOURCE_MISSING", f"data is absent and no valid prepare script is registered for {host_path or '(dynamic)'}", prepare or str(package_config.relative_to(mls_root))))
        normalized_package = package.lower().replace("-", "").replace("_", "")
        external_root = mls_root / "vendor" / "external_packages"
        source_matches = [
            candidate for candidate in external_root.iterdir()
            if candidate.is_dir() and candidate.name.lower().replace("-", "").replace("_", "") == normalized_package
        ] if external_root.is_dir() else []
        registry_entry = registry_by_normalized.get(normalized_package)
        if not source_matches and registry_entry:
            registry_name, registry_value = registry_entry
            issues.append(AuditIssue(
                "WARN",
                "PACKAGE_FETCH_REQUIRED",
                f"official fetch will prepare {registry_name} at {registry_value.get('commit', '(unrecorded commit)')}",
                "vendor/packages.yaml",
            ))
        elif len(source_matches) != 1:
            issues.append(AuditIssue(
                "BLOCKED",
                "EXTERNAL_PACKAGE_MISSING" if not source_matches else "EXTERNAL_PACKAGE_AMBIGUOUS",
                f"expected one source directory for package {package}, found {len(source_matches)}",
                "vendor/external_packages",
            ))
        elif registry_entry and registry_entry[1].get("url") != "local":
            expected_commit = registry_entry[1].get("commit", "")
            source_commit = _git(source_matches[0], "rev-parse", "HEAD")
            if not source_commit:
                issues.append(AuditIssue("BLOCKED", "EXTERNAL_PACKAGE_NOT_GIT", f"{source_matches[0].name} is not a Git checkout", str(source_matches[0].relative_to(mls_root))))
            elif expected_commit and not source_commit.startswith(expected_commit):
                issues.append(AuditIssue("WARN", "PACKAGE_CHECKOUT_REQUIRED", f"official fetch must switch {package} from {source_commit} to {expected_commit}", str(source_matches[0].relative_to(mls_root))))

    agent_shell_package = ""
    if commands:
        try:
            agent_shell_package = select_agent_image_package(commands, list(config.get("files", [])))
            if len(packages) > 1:
                issues.append(AuditIssue("INFO", "AGENT_IMAGE_SELECTED", f"multi-package task uses {agent_shell_package} as the Agent Bash image", "config.json"))
        except ValueError as exc:
            issues.append(AuditIssue("BLOCKED", "AGENT_IMAGE_AMBIGUOUS", str(exc), "config.json"))

    for baseline in config.get("baselines", {}).values():
        if isinstance(baseline, dict) and baseline.get("edit_ops"):
            issue = _validate_relative_file(task_root, str(baseline["edit_ops"]), "BASELINE_EDIT_MISSING")
            if issue:
                issues.append(issue)

    asset_manifest = _asset_manifest(asset_manifest_dir, task_id)
    registered_module_names = {Path(str(asset.get("destination", ""))).stem for asset in asset_manifest.get("assets", [])}
    issues = [
        issue
        for issue in issues
        if not (issue.code == "HOST_IMPORT_UNVERIFIED" and any(f"'{name}'" in issue.message for name in registered_module_names))
    ]
    for asset in asset_manifest.get("assets", []):
        relative = str(asset["destination"])
        destination = (mls_root / relative).resolve()
        try:
            destination.relative_to(mls_root.resolve())
        except ValueError:
            issues.append(AuditIssue("BLOCKED", "ASSET_PATH_ESCAPE", f"asset escapes MLS root: {relative}", relative))
            continue
        if not destination.is_file():
            source = (mls_root / str(asset.get("source", ""))).resolve()
            expected = str(asset.get("sha256", "")).lower()
            if source.is_file() and expected and _sha256(source) == expected:
                issues.append(AuditIssue("WARN", "ASSET_LOAD_REQUIRED", f"verified source is available for automatic loading into {relative}", relative))
            else:
                issues.append(AuditIssue("BLOCKED", "ASSET_MISSING", f"registered asset and/or verified source is missing: {relative}", relative))
            continue
        actual = _sha256(destination)
        expected = str(asset.get("sha256", "")).lower()
        if not expected or actual != expected:
            issues.append(AuditIssue("BLOCKED", "ASSET_HASH_MISMATCH", f"asset SHA256 is {actual}, expected {expected or '(unset)'}", relative))
            continue
        tracked = bool(_git(mls_root, "ls-files", "--error-unmatch", relative))
        issues.append(AuditIssue("INFO", "ASSET_READY", f"verified {'tracked' if tracked else 'external/untracked'} asset {actual}", relative))

    blocking = [issue for issue in issues if issue.level in {"ERROR", "BLOCKED"}]
    warnings = [issue for issue in issues if issue.level == "WARN"]
    status = "BLOCKED" if blocking else ("READY_WITH_WARNINGS" if warnings else "READY")
    return {
        "task": task_id,
        "status": status,
        "packages": sorted(packages),
        "agent_shell_package": agent_shell_package,
        "test_commands": len(commands),
        "host_imports": sorted(imports),
        "issues": [asdict(issue) for issue in issues],
    }


def audit_suite(mls_root: Path, manifest: Manifest, asset_manifest_dir: Path) -> dict[str, Any]:
    tasks = [audit_task(mls_root, task.id, asset_manifest_dir) for task in manifest.tasks]
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["status"]] = counts.get(task["status"], 0) + 1
    git_status = _git(mls_root, "status", "--porcelain").splitlines()
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mls_commit": _git(mls_root, "rev-parse", "HEAD"),
        "mls_dirty": bool(git_status),
        "mls_status": git_status,
        "suite": manifest.name,
        "task_count": len(tasks),
        "counts": counts,
        "tasks": tasks,
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MLS-Bench Lite 30 static preflight",
        "",
        f"- MLS commit: `{report['mls_commit']}`",
        f"- MLS worktree dirty while scanned: `{str(report['mls_dirty']).lower()}`",
        f"- Dirty paths: `{json.dumps(report.get('mls_status', []), ensure_ascii=False)}`",
        f"- Tasks: `{report['task_count']}`",
        f"- Counts: `{json.dumps(report['counts'], sort_keys=True)}`",
        "",
        "| Round | Task | Status | Packages | Blocking/warnings |",
        "|---:|---|---|---|---|",
    ]
    for index, task in enumerate(report["tasks"]):
        round_id = index // 6 + 1
        notable = [issue for issue in task["issues"] if issue["level"] in {"BLOCKED", "ERROR", "WARN"}]
        summary = "; ".join(f"{item['code']}: {item['message']}" for item in notable) or "-"
        summary = summary.replace("|", "\\|")
        lines.append(f"| {round_id} | `{task['task']}` | {task['status']} | {', '.join(task['packages']) or '-'} | {summary} |")
    lines.extend([
        "",
        "This is a local static audit. Docker image contents, API access and real GPU execution remain runtime checks.",
        "Task-local BLOCKED results are retryable and must not consume an Agent attempt.",
        "",
    ])
    return "\n".join(lines)


def write_reports(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, report_markdown(report))
