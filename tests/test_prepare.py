import unittest
import subprocess
from pathlib import Path
from unittest.mock import call, patch

from mls_lite_runner.prepare import execute_commands, prepare_commands


class PrepareCommandTests(unittest.TestCase):
    def test_docker_prepare_is_pull_only(self):
        commands = prepare_commands(
            Path("/env/bin/python"),
            Path("/mls"),
            Path("/config.yaml"),
            ["pkg-a"],
        )
        self.assertIn("--pull", commands[0])

    @patch("mls_lite_runner.prepare.time.sleep")
    @patch("mls_lite_runner.prepare.subprocess.run")
    def test_prepare_retries_twice_then_succeeds(self, run, sleep):
        command = ["python", "-m", "mlsbench.cli", "build", "pkg", "--pull"]
        run.side_effect = [
            subprocess.CalledProcessError(1, command),
            subprocess.CalledProcessError(1, command),
            subprocess.CompletedProcess(command, 0),
        ]

        execute_commands([command], Path("."), attempts=3, retry_delays=(20.0, 40.0))

        self.assertEqual(3, run.call_count)
        self.assertEqual([call(20.0), call(40.0)], sleep.call_args_list)

    @patch("mls_lite_runner.prepare.time.sleep")
    @patch("mls_lite_runner.prepare.subprocess.run")
    def test_prepare_stops_after_retry_budget(self, run, sleep):
        command = ["python", "-m", "mlsbench.cli", "build", "pkg", "--pull"]
        run.side_effect = subprocess.CalledProcessError(1, command)

        with self.assertRaises(subprocess.CalledProcessError):
            execute_commands([command], Path("."), attempts=3, retry_delays=(0.0, 0.0))

        self.assertEqual(3, run.call_count)
        self.assertEqual(2, sleep.call_count)


if __name__ == "__main__":
    unittest.main()
