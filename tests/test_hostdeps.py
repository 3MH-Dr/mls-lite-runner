import json
import unittest

from mls_lite_runner.hostdeps import audit_host_dependencies
from tests.support import case_directory


class HostDependencyTests(unittest.TestCase):
    def test_recurses_into_registered_asset_and_requires_approved_module(self):
        with case_directory() as root:
            mls = root / "mls"
            task = mls / "tasks" / "demo"
            holdout = mls / "holdout" / "demo"
            assets = root / "assets"
            task.mkdir(parents=True)
            holdout.mkdir(parents=True)
            assets.mkdir()
            (task / "parser.py").write_text("import dgp\n", encoding="utf-8")
            (task / "score_spec.py").write_text("VALUE = 1\n", encoding="utf-8")
            (task / "config.json").write_text("{}\n", encoding="utf-8")
            (holdout / "dgp.py").write_text("import surely_missing_mls_test_module\n", encoding="utf-8")
            (assets / "demo.json").write_text(json.dumps({
                "task": "demo",
                "assets": [{"destination": "holdout/demo/dgp.py"}],
            }), encoding="utf-8")
            registry = root / "host-imports.json"
            registry.write_text(json.dumps({
                "schema": 1,
                "modules": {
                    "surely_missing_mls_test_module": {"requirement": "approved-pkg==1", "tasks": ["demo"]}
                },
            }), encoding="utf-8")
            result = audit_host_dependencies(mls, "demo", assets, registry)
            self.assertFalse(result["ready"])
            self.assertEqual(["surely_missing_mls_test_module"], result["missing"])
            self.assertIn("dgp.py", " ".join(result["files"]))
