from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

    def finish(self, task_id: str, *, succeeded: bool, summary: Any = None, error: str | None = None) -> None:
        value = self.load()
        item = value["tasks"][task_id]
        if item["status"] != "running":
            raise ValueError(f"task {task_id} is not running")
        item["status"] = "succeeded" if succeeded else "failed"
        item["finished_at"] = utc_now()
        item["summary"] = summary
        item["last_error"] = error
        self._save(value)

    def pending_for_round(
        self,
        round_id: int,
        *,
        retry_failed: bool,
        task_ids: list[str] | None = None,
    ) -> list[str]:
        value = self.initialize()
        allowed = {"pending", "preflight_blocked"}
        if retry_failed:
            allowed.add("failed")
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

    def _save(self, value: dict[str, Any]) -> None:
        value["updated_at"] = utc_now()
        atomic_write_json(self.path, value)
