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


@dataclass(frozen=True)
class ResultClassification:
    status: str
    error: str | None
    evidence: dict[str, Any]


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


def _submission_metrics(submission: str) -> dict[str, Any]:
    marker = "[Leaderboard] Results saved:"
    for line in reversed(submission.splitlines()):
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1].strip()
        try:
            value = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def classify_result(returncode: int, summary: dict[str, Any] | None) -> ResultClassification:
    """Require MLS leaderboard metrics; process completion alone is not benchmark success."""
    evidence: dict[str, Any] = {
        "returncode": returncode,
        "done": summary.get("done") if summary else None,
        "tests": summary.get("tests") if summary else None,
        "exit_status": summary.get("exit_status") if summary else None,
    }
    if returncode != 0:
        return ResultClassification("failed", f"mlsbench exited with code {returncode}", evidence)
    if not summary:
        return ResultClassification("failed", "mlsbench emitted no parseable summary", evidence)
    if summary.get("done") is not True:
        return ResultClassification("failed", "agent summary has done != true", evidence)
    tests = summary.get("tests", 0)
    if not isinstance(tests, int) or tests < 1:
        return ResultClassification("failed", "agent summary reports no completed test", evidence)

    miniswe = summary.get("miniswe", {})
    submission = miniswe.get("submission", "") if isinstance(miniswe, dict) else ""
    submission = submission if isinstance(submission, str) else ""
    metrics = _submission_metrics(submission)
    failure_tokens = (
        "No valid metrics available",
        "[STATUS: FAILED",
        "[COMMAND FAILED",
        "[BUDGET CHECK FAILED]",
        "[PARSER FAILED",
    )
    failures = [token for token in failure_tokens if token in submission]
    evidence.update({
        "metric_count": len(metrics),
        "metric_names": sorted(str(key) for key in metrics),
        "failure_markers": failures,
        "leaderboard_results_marker": "[Leaderboard] Results saved:" in submission,
    })
    if not metrics:
        return ResultClassification(
            "invalid_submission",
            "MLS completed but saved no valid leaderboard metrics",
            evidence,
        )
    if failures:
        return ResultClassification(
            "submitted_partial",
            "MLS saved some metrics, but one or more official test environments failed",
            evidence,
        )
    return ResultClassification("succeeded", None, evidence)


def semantic_success(returncode: int, summary: dict[str, Any] | None) -> tuple[bool, str | None]:
    result = classify_result(returncode, summary)
    return result.status == "succeeded", result.error


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
