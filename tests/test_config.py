import unittest
from pathlib import Path

from mls_lite_runner.config import render_miniswe_config


class ConfigTests(unittest.TestCase):
    def test_direct_config_uses_litellm_and_has_no_key(self):
        text = render_miniswe_config(Path("/runs"))
        self.assertIn("model_class: litellm", text)
        self.assertNotIn("QueueProxyModel", text)
        self.assertNotIn("API_KEY", text)
