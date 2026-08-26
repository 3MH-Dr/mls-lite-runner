import hashlib
import json
import unittest

from mls_lite_runner.preflight import audit_task
from tests.support import case_directory


class PreflightTests(unittest.TestCase):
    def test_verified_registered_source_is_retryable_load_not_block(self):
        with case_directory() as root:
            mls = root / "mls"
            task = mls / "tasks" / "demo"
            package = mls / "vendor" / "pkg_configs" / "demo-pkg"
            external = mls / "vendor" / "external_packages" / "demo-pkg"
            manifests = root / "manifests"
            task.mkdir(parents=True)
            package.mkdir(parents=True)
            external.mkdir(parents=True)
            manifests.mkdir()
            (task / "config.json").write_text(json.dumps({
                "test_cmds": [{"cmd": "scripts/test.sh", "package": "demo-pkg"}],
                "baselines": {},
            }), encoding="utf-8")
            (task / "parser.py").write_text("import dgp\n", encoding="utf-8")
            (task / "score_spec.py").write_text("VALUE = 1\n", encoding="utf-8")
            (task / "task_description.md").write_text("demo\n", encoding="utf-8")
            (task / "scripts").mkdir()
            (task / "scripts/test.sh").write_text("true\n", encoding="utf-8")
            (package / "config.json").write_text("{}\n", encoding="utf-8")
            source = mls / "harbor" / "demo" / "dgp.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            (manifests / "demo.json").write_text(json.dumps({
                "task": "demo",
                "assets": [{"source": "harbor/demo/dgp.py", "destination": "holdout/demo/dgp.py", "sha256": digest}],
            }), encoding="utf-8")
            result = audit_task(mls, "demo", manifests)
            self.assertEqual("READY_WITH_WARNINGS", result["status"])
            self.assertIn("ASSET_LOAD_REQUIRED", {item["code"] for item in result["issues"]})
            self.assertNotIn("HOST_IMPORT_UNVERIFIED", {item["code"] for item in result["issues"]})

    def test_missing_test_script_blocks_only_the_task(self):
        with case_directory() as root:
            task = root / "mls/tasks/demo"
            package = root / "mls/vendor/pkg_configs/demo-pkg"
            external = root / "mls/vendor/external_packages/demo-pkg"
            task.mkdir(parents=True)
            package.mkdir(parents=True)
            external.mkdir(parents=True)
            (task / "config.json").write_text(json.dumps({
                "test_cmds": [{"cmd": "scripts/missing.sh", "package": "demo-pkg"}],
            }), encoding="utf-8")
            for name in ("parser.py", "score_spec.py", "task_description.md"):
                (task / name).write_text("\n", encoding="utf-8")
            (package / "config.json").write_text("{}\n", encoding="utf-8")
            result = audit_task(root / "mls", "demo", root / "manifests")
            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("TEST_SCRIPT_MISSING", {item["code"] for item in result["issues"]})
