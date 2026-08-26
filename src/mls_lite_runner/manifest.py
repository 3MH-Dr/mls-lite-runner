from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import read_json


@dataclass(frozen=True)
class TaskSpec:
    id: str
    round: int
    gpu_peak: int
    gpu_minimum: int
    allow_waves: bool = False
    notes: str = ""
    review_required: bool = False


@dataclass(frozen=True)
class RoundSpec:
    id: int
    tasks: tuple[TaskSpec, ...]
    platform_profile: str
    platform_gpus: int


@dataclass(frozen=True)
class Manifest:
    name: str
    rounds: tuple[RoundSpec, ...]

    @property
    def tasks(self) -> tuple[TaskSpec, ...]:
        return tuple(task for round_spec in self.rounds for task in round_spec.tasks)

    def round(self, round_id: int) -> RoundSpec:
        matches = [item for item in self.rounds if item.id == round_id]
        if len(matches) != 1:
            raise ValueError(f"round {round_id} does not exist")
        return matches[0]


def load_manifest(path: Path) -> Manifest:
    raw: dict[str, Any] = read_json(path)
    resources = raw.get("task_resources", {})
    rounds: list[RoundSpec] = []
    for round_raw in raw["rounds"]:
        round_id = int(round_raw["id"])
        tasks = tuple(
            TaskSpec(
                id=item["id"],
                round=round_id,
                gpu_peak=int(resources[item["id"]]["peak"]),
                gpu_minimum=int(resources[item["id"]]["minimum"]),
                allow_waves=bool(item.get("allow_waves", False)),
                notes=item.get("notes", ""),
                review_required=bool(item.get("review_required", False)),
            )
            for item in round_raw["tasks"]
        )
        rounds.append(
            RoundSpec(
                id=round_id,
                tasks=tasks,
                platform_profile=round_raw.get("platform_profile", "4090"),
                platform_gpus=int(round_raw.get("platform_gpus", 8)),
            )
        )
    manifest = Manifest(name=raw["name"], rounds=tuple(rounds))
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: Manifest) -> None:
    ids = [task.id for task in manifest.tasks]
    round_ids = [item.id for item in manifest.rounds]
    if round_ids != list(range(1, len(round_ids) + 1)):
        raise ValueError(f"round ids must be consecutive from 1, got {round_ids}")
    if len(ids) != 30:
        raise ValueError(f"Lite manifest must contain exactly 30 tasks, got {len(ids)}")
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate tasks: {', '.join(duplicates)}")
    if any(len(item.tasks) != 6 for item in manifest.rounds):
        raise ValueError("each Lite round must contain exactly 6 tasks")
    if any(item.platform_gpus not in {1, 2, 4, 8} for item in manifest.rounds):
        raise ValueError("platform_gpus must be one of 1, 2, 4, 8")
    for round_spec in manifest.rounds:
        for task in round_spec.tasks:
            if task.gpu_minimum > round_spec.platform_gpus:
                raise ValueError(
                    f"{task.id} needs at least {task.gpu_minimum} GPUs but round {round_spec.id} requests {round_spec.platform_gpus}"
                )
            if task.gpu_peak > round_spec.platform_gpus and not task.allow_waves:
                raise ValueError(
                    f"{task.id} peak={task.gpu_peak} exceeds round GPUs without allow_waves"
                )
