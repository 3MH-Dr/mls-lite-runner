from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import shutil

from .io import atomic_write_json, read_json
from .manifest import Manifest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SuiteState:
    def __init__(self, path: Path, manifest: Manifest):
        self.path = path
        self.manifest = manifest

    def initialize(self) -> dict[str, Any]:
        if self.path.exists():
            return self.load()
        value: dict[str, Any] = {
            "schema": 1,
            "suite": self.manifest.name,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "tasks": {
                task.id: {
                    "round": task.round,
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                    "summary": None,
                    "preflight_issues": [],
                }
                for task in self.manifest.tasks
            },
        }
        atomic_write_json(self.path, value)
        return value

    def load(self) -> dict[str, Any]:
        value = read_json(self.path)
        expected = {task.id for task in self.manifest.tasks}
        if set(value.get("tasks", {})) != expected:
            raise ValueError("state tasks do not match the current manifest")
        return value

    def recover_interrupted(self) -> list[str]:
        value = self.initialize()
        recovered: list[str] = []
        for task_id, item in value["tasks"].items():
            if item["status"] == "running":
                item["status"] = "pending"
                item["last_error"] = "previous process ended while task was running"
                recovered.append(task_id)
        if recovered:
            self._save(value)
        return recovered

    def start(self, task_id: str) -> int:
        value = self.initialize()
        item = value["tasks"][task_id]
        if item["status"] == "succeeded":
            raise ValueError(f"task {task_id} already succeeded")
        item["status"] = "running"
        item["attempts"] += 1
        item["started_at"] = utc_now()
        item["last_error"] = None
        item["preflight_issues"] = []
        self._save(value)
        return int(item["attempts"])

    def block(self, task_id: str, issues: list[str]) -> None:
        """Record a retryable task-local preflight block without consuming an attempt."""
        value = self.initialize()
        item = value["tasks"][task_id]
        if item["status"] == "succeeded":
            return
        item["status"] = "preflight_blocked"
        item["preflight_issues"] = list(issues)
        item["last_error"] = None
        item["checked_at"] = utc_now()
        self._save(value)

    def finish(
        self,
        task_id: str,
        *,
        status: str | None = None,
        succeeded: bool | None = None,
        summary: Any = None,
        error: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        value = self.load()
        item = value["tasks"][task_id]
        if item["status"] != "running":
            raise ValueError(f"task {task_id} is not running")
        if status is None:
            if succeeded is None:
                raise ValueError("finish requires status or succeeded")
            status = "succeeded" if succeeded else "failed"
        if status not in {"succeeded", "failed", "invalid_submission", "submitted_partial"}:
            raise ValueError(f"invalid terminal status: {status}")
        item["status"] = status
        item["finished_at"] = utc_now()
        item["summary"] = summary
        item["last_error"] = error
        item["result_evidence"] = evidence or {}
        self._save(value)

    def pending_for_round(
        self,
        round_id: int,
        *,
        retry_failed: bool,
        retry_partial: bool = False,
        task_ids: list[str] | None = None,
    ) -> list[str]:
        value = self.initialize()
        allowed = {"pending", "preflight_blocked"}
        if retry_failed:
            allowed.update({"failed", "invalid_submission"})
        if retry_partial:
            allowed.add("submitted_partial")
        selected = set(task_ids) if task_ids is not None else None
        return [
            task.id
            for task in self.manifest.round(round_id).tasks
            if (selected is None or task.id in selected)
            and value["tasks"][task.id]["status"] in allowed
        ]

    def round_summary(self, round_id: int, task_ids: list[str] | None = None) -> dict[str, Any]:
        value = self.initialize()
        selected = set(task_ids) if task_ids is not None else None
        tasks = {
            task.id: value["tasks"][task.id]
            for task in self.manifest.round(round_id).tasks
            if selected is None or task.id in selected
        }
        counts: dict[str, int] = {}
        for item in tasks.values():
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"round": round_id, "counts": counts, "tasks": tasks, "updated_at": value["updated_at"]}

    def reconcile_summaries(self, *, execute: bool) -> list[dict[str, str]]:
        """Reclassify old exit-code-only successes; preserve a timestamped state backup."""
        from .mls import classify_result

        value = self.initialize()
        actions: list[dict[str, str]] = []
        for task_id, item in value["tasks"].items():
            if item.get("status") != "succeeded" or not item.get("summary"):
                continue
            result = classify_result(0, item["summary"])
            if result.status == "succeeded":
                continue
            actions.append({"task": task_id, "from": "succeeded", "to": result.status})
            if execute:
                item["status"] = result.status
                item["last_error"] = result.error
                item["result_evidence"] = result.evidence
                item["reconciled_at"] = utc_now()
        if execute and actions:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.path.with_name(f"{self.path.name}.before-reconcile-{stamp}")
            shutil.copy2(self.path, backup)
            value.setdefault("reconciliation_history", []).append({
                "at": utc_now(), "backup": str(backup), "actions": actions,
            })
            self._save(value)
        return actions

    def _save(self, value: dict[str, Any]) -> None:
        value["updated_at"] = utc_now()
        atomic_write_json(self.path, value)
