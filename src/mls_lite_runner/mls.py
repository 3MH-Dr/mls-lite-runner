from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class RunSettings:
    python: Path
    mls_root: Path
    config: Path
    model: str
    runtime_root: Path


def agent_command(settings: RunSettings, task_id: str, attempt: int) -> list[str]:
    workspace = settings.runtime_root / "workspaces" / task_id / f"attempt-{attempt:03d}"
    return [
        str(settings.python),
        "-m",
        "mlsbench.cli",
        "agent",
        task_id,
        "--model",
        settings.model,
        "--config",
        str(settings.config),
        "--workspace",
        str(workspace),
        "--agent-type",
        "miniswe-bash",
    ]


def parse_summary(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        marker = "Summary:"
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1].strip()
        try:
            value = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return None
        return value if isinstance(value, dict) else None
    return None


def semantic_success(returncode: int, summary: dict[str, Any] | None) -> tuple[bool, str | None]:
    if returncode != 0:
        return False, f"mlsbench exited with code {returncode}"
    if not summary:
        return False, "mlsbench emitted no parseable summary"
    if summary.get("done") is not True:
        return False, "agent summary has done != true"
    tests = summary.get("tests", 0)
    if not isinstance(tests, int) or tests < 1:
        return False, "agent summary reports no completed test"
    return True, None


def run_agent(command: Sequence[str], *, cwd: Path, log_path: Path) -> tuple[int, str, dict[str, Any] | None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    chunks: list[str] = []
    assert process.stdout is not None
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
            chunks.append(line)
    returncode = process.wait()
    output = "".join(chunks)
    return returncode, output, parse_summary(output)

