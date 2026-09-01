import json
import unittest
from pathlib import Path

from mls_lite_runner.mounts import audit_task_mounts
from tests.support import case_directory


class MountTests(unittest.TestCase):
    def _fixture(self, root, bind):
        mls = root / "mls"
        task = mls / "tasks" / "demo"
        package = mls / "vendor" / "pkg_configs" / "demo-pkg"
        task.mkdir(parents=True)
        package.mkdir(parents=True)
        (task / "config.json").write_text(json.dumps({
            "test_cmds": [{"cmd": "true", "package": "demo-pkg"}],
        }), encoding="utf-8")
        (package / "config.json").write_text(json.dumps({"data_bind": bind}), encoding="utf-8")
        config = root / "runner.yaml"
        config.write_text("container_runtime: docker\n", encoding="utf-8")
        return mls, config

    def test_data_root_placeholder_expands_to_absolute_release_path(self):
        with case_directory() as root:
            mls, config = self._fixture(root, "{data_root}/sklearn:/data:ro")
            (mls / "vendor" / "data" / "sklearn").mkdir(parents=True)
            result = audit_task_mounts(mls, "demo", config)
            self.assertTrue(result["ready"], result["issues"])
            self.assertEqual(("vendor", "data"), Path(result["data_root"]).parts[-2:])

    def test_relative_bind_is_blocked_before_docker(self):
        with case_directory() as root:
            mls, config = self._fixture(root, "vendor/data/sklearn:/data")
            result = audit_task_mounts(mls, "demo", config)
            self.assertFalse(result["ready"])
            self.assertEqual("DOCKER_BIND_SOURCE_RELATIVE", result["issues"][0]["code"])

    def test_absolute_but_missing_bind_is_blocked_before_docker_creates_it(self):
        with case_directory() as root:
            mls, config = self._fixture(root, "{data_root}/missing:/data")
            result = audit_task_mounts(mls, "demo", config)
            self.assertFalse(result["ready"])
            self.assertIn("DOCKER_BIND_SOURCE_MISSING", {item["code"] for item in result["issues"]})

    def test_runner_mount_long_form_is_checked(self):
        with case_directory() as root:
            mls, config = self._fixture(root, None)
            config.write_text(
                "miniswe_bash:\n  environment:\n    run_args:\n      - --mount=type=bind,source=relative/path,target=/work\n",
                encoding="utf-8",
            )
            result = audit_task_mounts(mls, "demo", config)
            self.assertIn("DOCKER_BIND_SOURCE_RELATIVE", {item["code"] for item in result["issues"]})
