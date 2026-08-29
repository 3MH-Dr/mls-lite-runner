"""mini-swe-agent integration that uses Bash inside an MLS-Bench workspace."""

from __future__ import annotations

import os
from typing import Any

from mlsbench.agent.base import BaseAgent
from mls_agent.miniswe_bash_environment import MiniSWEBashEnvironment
from mls_agent.selection import select_agent_image_package
from mls_agent.typing_compat import enable_python310_typing_compat


SYSTEM_TEMPLATE = """You are an ML research coding agent solving one MLS-Bench task.

You have exactly one action tool: Bash. Each response must contain exactly one
Bash tool call. Obey the task's editable files and line ranges; Bash isolation
does not mechanically enforce those benchmark rules.

The shell runs in a restricted, persistent Docker workspace with no network,
no added capabilities and a read-only root filesystem. Use the exact standalone
command `mls-test` for official evaluation and `mls-submit N` to submit a one-based
test number (`-1` means latest). Never chain, redirect or pipe either gateway.
Never print mini-swe's generic completion token.
"""


_UNSAFE_DOCKER_OPTIONS = (
    "--privileged", "--network", "--cap-add", "--device", "--gpus",
    "--pid", "--ipc", "--uts", "--userns", "--mount", "--volume",
    "--security-opt", "--user", "--entrypoint", "-v",
)


def _sandbox_docker_run_args(run_args: list[str], uid: int, gid: int) -> list[str]:
    for argument in run_args:
        if not isinstance(argument, str):
            raise TypeError("miniswe_bash.environment.run_args entries must be strings")
        if any(argument == option or argument.startswith(option + "=") for option in _UNSAFE_DOCKER_OPTIONS):
            raise ValueError(f"Docker option {argument!r} is not allowed while sandbox=true")
    return [
        "--rm", *run_args, "--entrypoint", "", "--network", "none",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", "512", "--user", f"{uid}:{gid}", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=1g",
    ]


class MiniSWEBashAgent(BaseAgent):
    """Run mini-swe's loop while retaining MLS test/submission accounting."""

    agent_label = "miniswe-bash"

    def get_action(self, messages: list) -> dict | None:
        raise NotImplementedError("MiniSWEBashAgent uses mini-swe's DefaultAgent loop")

    def build_bash_task_prompt(self) -> str:
        prompt = super().build_initial_prompt()
        prompt = prompt.replace(
            "(every edit / test / undo / web_search / web_extract counts; submit does not)",
            "(one model turn equals one Bash action; submission is issued through Bash)",
        )
        prompt = prompt.replace(
            "(each test() call also consumes one action from the budget above)",
            "(each `mls-test` request also consumes one Bash action)",
        )
        prompt = prompt.replace("`test()`", "`mls-test`").replace("test()", "mls-test")
        return prompt + (
            "\n\n## Bash Agent Interface\n"
            "The shell starts in the prepared per-run task workspace.\n"
            "- Inspect and edit allowed code with ordinary Bash.\n"
            "- Evaluate with the standalone command `mls-test`.\n"
            "- Submit with `mls-submit N`; -1 selects the latest test.\n"
            "- The sandbox cannot enforce edit ranges; obey the stated restrictions.\n"
        )

    def _make_environment(self) -> MiniSWEBashEnvironment:
        from minisweagent.environments import get_environment

        root = self.tools.workspace_task_dir.resolve()
        settings = dict(self.global_config.get("miniswe_bash", {}).get("environment", {}))
        environment_type = settings.pop("type", "local")
        settings.pop("environment_class", None)
        if environment_type == "local":
            if not settings.pop("allow_unsafe_local", False):
                raise ValueError("type=local is not isolated; set allow_unsafe_local=true explicitly")
            settings["cwd"] = str(root)
        elif environment_type == "docker":
            image = settings.get("image", "auto")
            if image == "auto":
                package = select_agent_image_package(self.tools.test_cmd_entries, self.config_edit)
                from mlsbench.cli import docker_image_tag
                image = docker_image_tag(package)
            settings["image"] = image
            sandbox = bool(settings.pop("sandbox", True))
            run_args = list(settings.get("run_args", []))
            if sandbox:
                run_args = _sandbox_docker_run_args(run_args, os.getuid(), os.getgid())
                environment_vars = dict(settings.get("env", {}))
                environment_vars.setdefault("HOME", "/tmp/agent-home")
                environment_vars.setdefault("PYTHONDONTWRITEBYTECODE", "1")
                settings["env"] = environment_vars
            else:
                run_args = ["--rm", "--entrypoint", "", *run_args]
            run_args.extend(["-v", f"{root}:/workspace:rw"])
            settings["run_args"] = run_args
            settings["cwd"] = "/workspace"
        else:
            raise ValueError("miniswe_bash.environment.type must be 'local' or 'docker'")
        environment = get_environment({"environment_class": environment_type, **settings})
        return MiniSWEBashEnvironment(environment, self.tools)

    def run(self, resume: bool = False) -> dict:
        if resume:
            raise NotImplementedError("--resume is not supported by agent-type miniswe-bash")
        enable_python310_typing_compat()
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.models import get_model

        self.setup_workspace()
        self.logger.reset()
        task_prompt = self.build_bash_task_prompt()
        self.logger.log_initial_prompt(task_prompt)
        settings = dict(self.global_config.get("miniswe_bash", {}))
        model_settings = dict(settings.get("model", {}))
        agent_settings = dict(settings.get("agent", {}))
        model = get_model(self.global_config.get("model"), config=model_settings)
        environment = self._make_environment()
        trajectory_path = self.logger.log_dir / "miniswe.traj.json"
        agent = DefaultAgent(
            model,
            environment,
            system_template=SYSTEM_TEMPLATE,
            instance_template="{{ task }}",
            step_limit=int(agent_settings.get("step_limit", self.max_steps)),
            cost_limit=float(agent_settings.get("cost_limit", 0.0)),
            wall_time_limit_seconds=int(agent_settings.get("wall_time_limit_seconds", 18000)),
            max_consecutive_format_errors=int(agent_settings.get("max_consecutive_format_errors", 3)),
            output_path=trajectory_path,
        )
        result: dict[str, Any] = {}
        error: str | None = None
        try:
            result = agent.run(task_prompt)
            return {"steps": agent.n_calls, "tests": self.tools.test_count, "done": self.tools.done, "miniswe": result}
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            environment.cleanup()
            self.tools.record_zero_if_no_finals()
            self._write_run_summary(
                error=error,
                extra={"miniswe": {
                    "exit_status": result.get("exit_status", ""),
                    "submission": result.get("submission", ""),
                    "model_calls": agent.n_calls,
                    "cost": agent.cost,
                    "trajectory": str(trajectory_path),
                }},
            )
