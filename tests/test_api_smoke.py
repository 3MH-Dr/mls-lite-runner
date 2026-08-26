import sys
import types
import unittest
from argparse import Namespace
from unittest.mock import patch

from mls_lite_runner.cli import cmd_api_smoke
from support import case_directory


class ApiSmokeTests(unittest.TestCase):
    def test_uses_direct_litellm_config_and_suppresses_content(self):
        calls = []

        class Model:
            def query(self, messages):
                calls.append(messages)
                return {"role": "assistant", "content": "private model response"}

        def get_model(name, config):
            self.assertEqual("deepseek/example", name)
            self.assertEqual("litellm", config["model_class"])
            return Model()

        package = types.ModuleType("minisweagent")
        package.__path__ = []
        models = types.ModuleType("minisweagent.models")
        models.get_model = get_model
        with case_directory() as root:
            config = root / "config.yaml"
            config.write_text(
                "miniswe_bash:\n  model:\n    model_class: litellm\n",
                encoding="utf-8",
            )
            with patch.dict(
                sys.modules,
                {"minisweagent": package, "minisweagent.models": models},
            ):
                result = cmd_api_smoke(
                    Namespace(config=str(config), model="deepseek/example")
                )

        self.assertEqual(0, result)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
