import unittest

from mls_agent.selection import select_agent_image_package


class AgentImageSelectionTests(unittest.TestCase):
    def test_single_package(self):
        self.assertEqual("deap", select_agent_image_package([{"package": "deap"}], []))

    def test_multi_package_uses_unique_file_owner(self):
        result = select_agent_image_package(
            [{"package": "nanoGPT"}, {"package": "lm-evaluation-harness"}],
            [{"filename": "nanoGPT/custom_pretrain.py"}, {"filename": "nanoGPT/model.py"}],
        )
        self.assertEqual("nanoGPT", result)

    def test_ambiguous_multi_package_refuses_guess(self):
        with self.assertRaises(ValueError):
            select_agent_image_package(
                [{"package": "one"}, {"package": "two"}],
                [{"filename": "one/a.py"}, {"filename": "two/b.py"}],
            )
