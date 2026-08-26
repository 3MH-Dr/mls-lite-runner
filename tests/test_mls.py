import unittest
from pathlib import Path

from mls_lite_runner.mls import RunSettings, agent_command, parse_summary, semantic_success


class MLSTests(unittest.TestCase):
    def test_real_cli_shape_is_used(self):
        settings = RunSettings(Path("/env/bin/python"), Path("/mls"), Path("/config.yaml"), "model-x", Path("/runtime"))
        command = agent_command(settings, "task-x", 2)
        self.assertEqual(["agent", "task-x"], command[3:5])
        self.assertIn("miniswe-bash", command)
        self.assertIn("attempt-002", command[command.index("--workspace") + 1])

    def test_exit_zero_done_false_is_failure(self):
        summary = parse_summary("[done] Summary: {'steps': 7, 'tests': 0, 'done': False}\n")
        self.assertEqual((False, "agent summary has done != true"), semantic_success(0, summary))

    def test_done_with_test_is_success(self):
        summary = parse_summary("[done] Summary: {'steps': 7, 'tests': 1, 'done': True}\n")
        self.assertEqual((True, None), semantic_success(0, summary))

