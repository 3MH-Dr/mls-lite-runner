import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlatformEntryTests(unittest.TestCase):
    def test_release_bootstrap_is_isolated_and_guarded(self):
        text = (ROOT / "platform" / "qz_entry.sh").read_text(encoding="utf-8")
        self.assertIn("mlsbench-lite-agent-v001/bin/python", text)
        self.assertIn("mlsbench-lite-agent/bin/python", text)
        self.assertIn("-m venv --copies", text)
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
