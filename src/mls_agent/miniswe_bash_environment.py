"""mini-swe Bash environment adapter for MLS-Bench test and submit calls."""

from __future__ import annotations

import re
from typing import Any


_SUBMIT_RE = re.compile(r"mls-submit\s+(-?\d+)\Z")
_MINISWE_COMPLETION_TOKEN = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class MiniSWEBashEnvironment:
    """Delegate Bash while routing exact MLS gateway commands to WorkspaceTools."""

    def __init__(self, environment: Any, workspace_tools: Any):
        self.environment = environment
        self.workspace_tools = workspace_tools
        self.config = environment.config

    @staticmethod
    def _observation(output: str, *, returncode: int = 0) -> dict[str, Any]:
        return {"output": output, "returncode": returncode, "exception_info": ""}

    def execute(self, action: dict, cwd: str = "") -> dict[str, Any]:
        command = action.get("command", "")
        if not isinstance(command, str):
            return self._observation("ERROR: Bash action 'command' must be a string.", returncode=2)
        command = command.strip()
        if _MINISWE_COMPLETION_TOKEN in command:
            return self._observation("ERROR: mini-swe's completion token is disabled. Use `mls-submit N`.", returncode=2)
        if command == "mls-test":
            result = str(self.workspace_tools.dispatch("test", {}))
            return self._observation(result, returncode=int(result.startswith("ERROR:")))
        submit_match = _SUBMIT_RE.fullmatch(command)
        if submit_match:
            test_number = int(submit_match.group(1))
            result = str(self.workspace_tools.dispatch("submit", {"n": test_number}))
            if self.workspace_tools.done:
                from minisweagent.exceptions import Submitted
                raise Submitted({"role": "exit", "content": result, "extra": {"exit_status": "Submitted", "submission": result}})
            return self._observation(result, returncode=int(result.startswith("ERROR:")))
        if "mls-test" in command or "mls-submit" in command:
            return self._observation(
                "ERROR: `mls-test` and `mls-submit N` must be the entire Bash command; do not combine them.",
                returncode=2,
            )
        return self.environment.execute(action, cwd=cwd)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        values = self.environment.get_template_vars(**kwargs)
        return {**values, "mls_test_budget": self.workspace_tools.max_tests, "mls_workspace": str(self.workspace_tools.workspace_task_dir)}

    def serialize(self) -> dict[str, Any]:
        data = self.environment.serialize()
        data.setdefault("info", {}).setdefault("config", {})["mls_bash_gateway"] = {
            "test_command": "mls-test", "submit_command": "mls-submit N", "edit_validation": "prompt_only"
        }
        return data

    def cleanup(self) -> None:
        cleanup = getattr(self.environment, "cleanup", None)
        if callable(cleanup):
            cleanup()
