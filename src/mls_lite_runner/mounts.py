from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(frozen=True)
class MountIssue:
    code: str
    message: str
    path: str = ""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _expand(value: str, *, mls_root: Path, data_root: Path) -> str:
    return value.replace("{project_root}", str(mls_root)).replace("{data_root}", str(data_root))


def _iter_binds(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        yield from (item for item in value if isinstance(item, str))


def _validate_source(raw: str, *, mls_root: Path, data_root: Path, origin: str) -> list[MountIssue]:
    expanded = _expand(raw, mls_root=mls_root, data_root=data_root)
    source = Path(expanded)
    issues: list[MountIssue] = []
    if not source.is_absolute():
        return [MountIssue(
            "DOCKER_BIND_SOURCE_RELATIVE",
            f"Docker host source must be absolute, got {expanded!r}; relative values become invalid named volumes",
            origin,
        )]
    if not _inside(source, mls_root):
        issues.append(MountIssue(
            "DOCKER_BIND_SOURCE_OUTSIDE_RELEASE",
            f"Docker host source resolves outside the pinned MLS checkout: {source.resolve()}",
            origin,
        ))
    elif not source.exists():
        issues.append(MountIssue(
            "DOCKER_BIND_SOURCE_MISSING",
            f"Docker host source does not exist after package preparation: {source}",
            origin,
        ))
    return issues


def _validate_bind(raw: str, *, mls_root: Path, data_root: Path, origin: str) -> list[MountIssue]:
    if ":" not in raw:
        return [MountIssue("DOCKER_BIND_MALFORMED", f"bind has no container destination: {raw!r}", origin)]
    source, remainder = raw.split(":", 1)
    destination = remainder.split(":", 1)[0]
    issues = _validate_source(source, mls_root=mls_root, data_root=data_root, origin=origin)
    if not destination.startswith("/"):
        issues.append(MountIssue(
            "DOCKER_BIND_DESTINATION_RELATIVE",
            f"Docker container destination must be absolute, got {destination!r}",
            origin,
        ))
    return issues


def _run_arg_binds(run_args: Any) -> Iterable[str]:
    if not isinstance(run_args, list):
        return
    index = 0
    while index < len(run_args):
        token = str(run_args[index])
        if token in {"-v", "--volume"} and index + 1 < len(run_args):
            yield str(run_args[index + 1])
            index += 2
            continue
        if token.startswith("--volume="):
            yield token.split("=", 1)[1]
        mount_value = None
        if token == "--mount" and index + 1 < len(run_args):
            mount_value = str(run_args[index + 1])
            index += 1
        elif token.startswith("--mount="):
            mount_value = token.split("=", 1)[1]
        if mount_value is not None:
            fields = dict(
                field.split("=", 1) for field in mount_value.split(",") if "=" in field
            )
            source = fields.get("src", fields.get("source"))
            target = fields.get("dst", fields.get("destination", fields.get("target")))
            if source and target:
                yield f"{source}:{target}"
        index += 1


def audit_task_mounts(mls_root: Path, task_id: str, runner_config: Path) -> dict[str, object]:
    """Validate every configured bind/copy source before Docker can reinterpret it as a volume name."""
    issues: list[MountIssue] = []
    runtime_config = yaml.safe_load(runner_config.read_text(encoding="utf-8")) or {}
    raw_data_root = runtime_config.get("data_root")
    if raw_data_root is None:
        data_root = (mls_root / "vendor" / "data").resolve()
    else:
        candidate = Path(str(raw_data_root))
        if not candidate.is_absolute():
            issues.append(MountIssue(
                "DATA_ROOT_RELATIVE",
                f"runner data_root must be absolute or omitted, got {raw_data_root!r}",
                str(runner_config),
            ))
            data_root = candidate
        else:
            data_root = candidate.resolve()
            if not _inside(data_root, mls_root):
                issues.append(MountIssue(
                    "DATA_ROOT_OUTSIDE_RELEASE",
                    f"runner data_root is outside the pinned MLS checkout: {data_root}",
                    str(runner_config),
                ))

    environment = runtime_config.get("miniswe_bash", {}).get("environment", {})
    checked: list[dict[str, str]] = []
    for bind in _run_arg_binds(environment.get("run_args", [])):
        checked.append({"kind": "runner.run_args", "package": "(runner)", "source": bind.split(":", 1)[0]})
        issues.extend(_validate_bind(bind, mls_root=mls_root, data_root=data_root, origin=str(runner_config)))

    task_config_path = mls_root / "tasks" / task_id / "config.json"
    task_config = json.loads(task_config_path.read_text(encoding="utf-8"))
    packages = sorted({str(item["package"]) for item in task_config.get("test_cmds", []) if item.get("package")})
    for package in packages:
        path = mls_root / "vendor" / "pkg_configs" / package / "config.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(MountIssue("PACKAGE_CONFIG_INVALID", f"{type(exc).__name__}: {exc}", str(path)))
            continue
        for bind in _iter_binds(config.get("data_bind")):
            # The source is the first field. Container destination/options cannot repair a relative source.
            source = bind.split(":", 1)[0]
            checked.append({"kind": "data_bind", "package": package, "source": source})
            issues.extend(_validate_bind(bind, mls_root=mls_root, data_root=data_root, origin=str(path)))
        for dependency in config.get("data_deps", []):
            if not isinstance(dependency, dict):
                continue
            for key in ("host_path", "ready_files"):
                values = dependency.get(key, [])
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    continue
                for value in values:
                    if not isinstance(value, str) or not value:
                        continue
                    checked.append({"kind": f"data_deps.{key}", "package": package, "source": value})
                    issues.extend(_validate_source(value, mls_root=mls_root, data_root=data_root, origin=str(path)))
    return {
        "task": task_id,
        "data_root": str(data_root),
        "packages": packages,
        "checked": checked,
        "issues": [issue.__dict__ for issue in issues],
        "ready": not issues,
    }
