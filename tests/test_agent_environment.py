import sys
import types
import unittest
from unittest.mock import patch

from mls_agent.miniswe_bash_environment import MiniSWEBashEnvironment


class _WrappedEnvironment:
    config = {"environment_class": "docker"}

    def __init__(self):
        self.actions = []
        self.cleaned = False

    def execute(self, action, cwd=""):
        self.actions.append((action, cwd))
        return {"output": "ordinary bash", "returncode": 0, "exception_info": ""}

    def get_template_vars(self, **kwargs):
        return {"wrapped": True, **kwargs}

    def serialize(self):
        return {"info": {"config": {}}}

    def cleanup(self):
        self.cleaned = True


class _WorkspaceTools:
    max_tests = 3
    workspace_task_dir = "/workspace"

    def __init__(self):
        self.done = False
        self.calls = []

    def dispatch(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "submit":
            self.done = True
            return "submitted"
        return "test-result"


class AgentEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.wrapped = _WrappedEnvironment()
        self.tools = _WorkspaceTools()
        self.environment = MiniSWEBashEnvironment(self.wrapped, self.tools)

    def test_routes_test_and_forwards_ordinary_bash(self):
        result = self.environment.execute({"command": "mls-test"})
        self.assertEqual("test-result", result["output"])
        self.assertEqual([("test", {})], self.tools.calls)

        result = self.environment.execute({"command": "python train.py"}, cwd="/workspace")
        self.assertEqual("ordinary bash", result["output"])
        self.assertEqual([({"command": "python train.py"}, "/workspace")], self.wrapped.actions)

    def test_rejects_compound_gateway_command(self):
        result = self.environment.execute({"command": "mls-test && echo bypass"})
        self.assertEqual(2, result["returncode"])
        self.assertEqual([], self.tools.calls)
        self.assertEqual([], self.wrapped.actions)

    def test_successful_submit_terminates_miniswe_loop(self):
        class Submitted(Exception):
            pass

        package = types.ModuleType("minisweagent")
        package.__path__ = []
        exceptions = types.ModuleType("minisweagent.exceptions")
        exceptions.Submitted = Submitted
        with patch.dict(
            sys.modules,
            {"minisweagent": package, "minisweagent.exceptions": exceptions},
        ):
            with self.assertRaises(Submitted):
                self.environment.execute({"command": "mls-submit -1"})

        self.assertEqual([("submit", {"n": -1})], self.tools.calls)


if __name__ == "__main__":
    unittest.main()
