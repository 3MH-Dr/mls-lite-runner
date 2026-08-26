import unittest
from pathlib import Path

from mls_lite_runner.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_lite_has_five_rounds_and_thirty_unique_tasks(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        self.assertEqual(5, len(manifest.rounds))
        self.assertEqual([6, 6, 6, 6, 6], [len(item.tasks) for item in manifest.rounds])
        self.assertEqual(30, len({task.id for task in manifest.tasks}))

    def test_4090_round_gpu_plan_matches_heaviest_task(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        self.assertTrue(all(item.platform_profile == "4090" for item in manifest.rounds))
        self.assertEqual([1, 8, 4, 1, 4], [item.platform_gpus for item in manifest.rounds])
        dbm = next(task for task in manifest.tasks if task.id == "cv-dbm-sampler")
        self.assertEqual((12, 4, True), (dbm.gpu_peak, dbm.gpu_minimum, dbm.allow_waves))
        vae = next(task for task in manifest.tasks if task.id == "cv-vae-loss")
        self.assertEqual((8, 8), (vae.gpu_peak, vae.gpu_minimum))
