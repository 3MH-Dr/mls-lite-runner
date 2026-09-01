import unittest
from pathlib import Path

from mls_lite_runner.manifest import load_manifest
from mls_lite_runner.state import SuiteState
from tests.support import case_directory


ROOT = Path(__file__).resolve().parents[1]


class StateTests(unittest.TestCase):
    def test_interrupted_task_is_recoverable_and_attempt_is_incremented(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.tasks[0].id
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            self.assertEqual(1, state.start(task))
            self.assertEqual([task], state.recover_interrupted())
            self.assertEqual(2, state.start(task))
            state.finish(task, succeeded=True, summary={"done": True, "tests": 1})
            value = state.load()["tasks"][task]
            self.assertEqual("succeeded", value["status"])
            self.assertEqual(2, value["attempts"])

    def test_succeeded_tasks_are_not_pending(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.round(1).tasks[0].id
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            state.start(task)
            state.finish(task, succeeded=True, summary={"done": True, "tests": 1})
            self.assertNotIn(task, state.pending_for_round(1, retry_failed=True))

    def test_preflight_block_does_not_consume_attempt_and_is_rechecked(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.round(1).tasks[0].id
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            state.block(task, ["ASSET_MISSING: dgp.py"])
            item = state.load()["tasks"][task]
            self.assertEqual("preflight_blocked", item["status"])
            self.assertEqual(0, item["attempts"])
            self.assertIn(task, state.pending_for_round(1, retry_failed=False))

    def test_round_selection_only_returns_and_reports_explicit_tasks(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        selected = [task.id for task in manifest.round(1).tasks[:2]]
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            self.assertEqual(
                selected,
                state.pending_for_round(1, retry_failed=False, task_ids=selected),
            )
            self.assertEqual(selected, list(state.round_summary(1, selected)["tasks"]))

    def test_invalid_submission_retries_only_with_retry_failed(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.round(1).tasks[0].id
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            state.start(task)
            state.finish(task, status="invalid_submission", error="no metrics")
            self.assertNotIn(task, state.pending_for_round(1, retry_failed=False))
            self.assertIn(task, state.pending_for_round(1, retry_failed=True))

    def test_partial_submission_requires_explicit_retry_partial(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.round(1).tasks[0].id
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            state.start(task)
            state.finish(task, status="submitted_partial", error="one environment failed")
            self.assertNotIn(task, state.pending_for_round(1, retry_failed=True))
            self.assertIn(task, state.pending_for_round(1, retry_failed=False, retry_partial=True))

    def test_reconcile_old_false_success_is_dry_run_then_backed_up(self):
        manifest = load_manifest(ROOT / "manifests" / "lite30.json")
        task = manifest.round(1).tasks[0].id
        summary = {
            "done": True,
            "tests": 2,
            "miniswe": {"submission": "[submit] No valid metrics available to submit."},
        }
        with case_directory() as temp:
            state = SuiteState(temp / "state.json", manifest)
            state.start(task)
            state.finish(task, succeeded=True, summary=summary)
            self.assertEqual("invalid_submission", state.reconcile_summaries(execute=False)[0]["to"])
            self.assertEqual("succeeded", state.load()["tasks"][task]["status"])
            state.reconcile_summaries(execute=True)
            value = state.load()
            self.assertEqual("invalid_submission", value["tasks"][task]["status"])
            self.assertTrue(list(temp.glob("state.json.before-reconcile-*")))
