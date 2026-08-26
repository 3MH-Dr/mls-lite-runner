import hashlib
import json
import unittest
from unittest.mock import patch

from mls_lite_runner.assets import prepare_task_assets, unload_task_assets
from tests.support import case_directory


COMMIT = "cfd57a7e0139c72753e32e31bca593719b098717"


class AssetTests(unittest.TestCase):
    def test_verified_asset_is_idempotent_and_reversible(self):
        with case_directory() as root:
            mls = root / "mls"
            source_root = root / "source"
            manifests = root / "manifests"
            receipts = root / "receipts"
            source = source_root / "task" / "dgp.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifests.mkdir()
            (manifests / "task.json").write_text(
                json.dumps({
                    "task": "task", "compatible_mls_commits": [COMMIT],
                    "assets": [{"source": "task/dgp.py", "destination": "holdout/task/dgp.py", "sha256": digest}],
                }), encoding="utf-8",
            )
            with patch("mls_lite_runner.assets._commit", return_value=COMMIT):
                ready, _ = prepare_task_assets(
                    "task", asset_manifest_dir=manifests, source_root=source_root,
                    mls_root=mls, receipt_root=receipts, execute=True,
                )
                self.assertTrue(ready)
                ready_again, _ = prepare_task_assets(
                    "task", asset_manifest_dir=manifests, source_root=source_root,
                    mls_root=mls, receipt_root=receipts, execute=True,
                )
                self.assertTrue(ready_again)
            safe, _ = unload_task_assets("task", mls_root=mls, receipt_root=receipts, execute=True)
            self.assertTrue(safe)
            self.assertFalse((mls / "holdout/task/dgp.py").exists())

    def test_missing_source_blocks_without_partial_file(self):
        with case_directory() as root:
            manifests = root / "manifests"
            manifests.mkdir()
            (manifests / "task.json").write_text(
                json.dumps({
                    "task": "task", "compatible_mls_commits": [COMMIT],
                    "assets": [{"source": "missing", "destination": "holdout/task/dgp.py", "sha256": "0" * 64}],
                }), encoding="utf-8",
            )
            with patch("mls_lite_runner.assets._commit", return_value=COMMIT):
                ready, _ = prepare_task_assets(
                    "task", asset_manifest_dir=manifests, source_root=root / "source",
                    mls_root=root / "mls", receipt_root=root / "receipts", execute=True,
                )
            self.assertFalse(ready)
            self.assertFalse((root / "mls/holdout/task/dgp.py").exists())
