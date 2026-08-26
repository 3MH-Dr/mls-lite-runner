from __future__ import annotations

import json
import importlib.util
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest


@dataclass(frozen=True)
class Check:
    level: str
    subject: str
    message: str


def inspect_task(mls_root: Path, task_id: str) -> tuple[set[str], float, list[Check]]:
    task_root = mls_root / "tasks" / task_id
    config_path = task_root / "config.json"
    checks: list[Check] = []
    if not config_path.is_file():
        return set(), 0.0, [Check("BLOCKED", task_id, f"missing {config_path}")]
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), 0.0, [Check("BLOCKED", task_id, f"invalid config.json: {exc}")]
    commands = config.get("test_cmds", [])
    packages = {item["package"] for item in commands if item.get("package")}
    grouped: dict[object, float] = {}
    for item in commands:
        group = item.get("group", item.get("label", "ungrouped"))
        grouped[group] = grouped.get(group, 0.0) + float(item.get("compute", 0.0))
    max_compute = max(grouped.values(), default=0.0)
    if not packages:
        checks.append(Check("BLOCKED", task_id, "no package declared in test_cmds"))
    for relative in ("parser.py", "score_spec.py"):
        if not (task_root / relative).is_file():
            checks.append(Check("BLOCKED", task_id, f"missing {relative}"))
    return packages, max_compute, checks


def run_doctor(manifest: Manifest, mls_root: Path, agent_root: Path | None, python: Path | None) -> list[Check]:
    checks: list[Check] = []
    if not (mls_root / "src" / "mlsbench" / "cli.py").is_file():
        checks.append(Check("ERROR", "MLS-Bench", "src/mlsbench/cli.py is missing"))
        return checks
    cli_text = (mls_root / "src" / "mlsbench" / "cli.py").read_text(encoding="utf-8", errors="replace")
    if "miniswe-bash" not in cli_text or "from mls_agent.miniswe_bash_agent import MiniSWEBashAgent" not in cli_text:
        checks.append(Check("ERROR", "adapter", "MLS CLI does not register the external mls_agent package"))
    if agent_root is not None and not (agent_root / "src" / "mls_agent" / "miniswe_bash_agent.py").is_file():
        checks.append(Check("ERROR", "mls-agent", f"external Agent source is missing under {agent_root}"))
    if python is not None and not python.is_file():
        checks.append(Check("ERROR", "python", f"interpreter missing: {python}"))
    if importlib.util.find_spec("minisweagent") is None:
        checks.append(Check("ERROR", "mini-swe-agent", "minisweagent is not importable in the active Python environment"))
    if importlib.util.find_spec("mls_agent") is None:
        checks.append(Check("ERROR", "mls-agent", "external mls_agent is not importable in the active Python environment"))
    if importlib.util.find_spec("numpy") is None:
        checks.append(Check("ERROR", "host-python", "numpy is required by multiple Lite task parsers but is not importable"))
    if shutil.which("docker") is None:
        checks.append(Check("ERROR", "docker", "docker CLI is missing; the 4090 MLS task sandbox cannot run"))
    for task in manifest.tasks:
        packages, max_compute, task_checks = inspect_task(mls_root, task.id)
        checks.extend(task_checks)
        try:
            from mlsbench.scheduler import infer_gpus_needed, infer_min_gpus_needed

            actual_peak = int(infer_gpus_needed(task.id))
            actual_minimum = int(infer_min_gpus_needed(task.id))
            if (actual_peak, actual_minimum) != (task.gpu_peak, task.gpu_minimum):
                checks.append(
                    Check(
                        "ERROR",
                        task.id,
                        f"GPU metadata changed: manifest peak/min={task.gpu_peak}/{task.gpu_minimum}, "
                        f"current MLS={actual_peak}/{actual_minimum}",
                    )
                )
        except (ImportError, OSError, ValueError) as exc:
            checks.append(Check("ERROR", task.id, f"cannot verify MLS GPU requirements: {exc}"))
        checks.append(
            Check(
                "INFO",
                task.id,
                f"packages={','.join(sorted(packages)) or '-'}; MLS peak/min GPUs={task.gpu_peak}/{task.gpu_minimum}; "
                f"raw max grouped compute={max_compute:g}",
            )
        )
        round_spec = manifest.round(task.round)
        if task.gpu_minimum > round_spec.platform_gpus:
            checks.append(Check("ERROR", task.id, "round GPU allocation is below the largest single test command"))
        elif task.gpu_peak > round_spec.platform_gpus:
            level = "WARN" if task.allow_waves else "ERROR"
            checks.append(
                Check(
                    level,
                    task.id,
                    f"peak {task.gpu_peak} exceeds visible {round_spec.platform_gpus}; "
                    + ("MLS will execute supported group waves" if task.allow_waves else "waves are not approved"),
                )
            )
        if task.review_required:
            checks.append(Check("WARN", task.id, task.notes or "manual resource review required"))
    config = mls_root / "configs" / "miniswe_bash.yaml"
    if config.is_file():
        text = config.read_text(encoding="utf-8", errors="replace")
        if re.search(r"save_path:\s*[\"']?/(?!inspire/)", text):
            checks.append(Check("WARN", "config", "save_path is an absolute non-platform path; generate a platform config"))
    return checks
