import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlatformEntryTests(unittest.TestCase):
    def test_shared_environment_is_registered_locked_and_guarded(self):
        text = (ROOT / "platform" / "qz_entry.sh").read_text(encoding="utf-8")
        self.assertIn('SHARED_ENV="$ROOT/runtime/envs/mlsbench-lite-agent"', text)
        self.assertIn('PYTHON="$SHARED_ENV/bin/python"', text)
        self.assertIn("runtime/env-registry/mlsbench-lite-agent", text)
        self.assertIn("environment-receipt.json", text)
        self.assertIn('flock -x 9', text)
        self.assertIn('flock -s -n 8', text)
        self.assertIn('--dry-run --report', text)
        self.assertIn('resolved-constraints.txt', text)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', text)
        self.assertNotIn("-m venv", text)
        self.assertNotIn("-m virtualenv", text)
        self.assertNotIn("pip install -e", text)
        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertNotIn("sys.version_info >= (3, 11)", text)
        self.assertIn("PREPARE_RELEASE_OK", text)
        self.assertIn("release is incomplete; marker is missing", text)
        self.assertNotIn("conda create", text)

    def test_configs_are_separate_per_round(self):
        text = (ROOT / "platform" / "qz_entry.sh").read_text(encoding="utf-8")
        self.assertIn('round_config() {', text)
        self.assertIn('--config "$config"', text)
        self.assertIn('"$root/state.json"', text)


if __name__ == "__main__":
    unittest.main()
