import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlatformScriptTests(unittest.TestCase):
    def test_docker_jobs_request_platform_docker(self):
        for name in (
            "submit-4090-smoke.ps1",
            "submit-run-task.ps1",
            "submit-run-round.ps1",
        ):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("--profile 4090 --docker --gpus", text, name)

    def test_cpu_report_uses_supported_cpu_size(self):
        text = (ROOT / "scripts" / "submit-round-report.ps1").read_text(encoding="utf-8")
        self.assertIn("--cpu-spec 4c16g", text)
        self.assertNotIn("--cpu-spec 1c4g", text)

    def test_release_python_ignores_base_image_torch_libraries(self):
        text = (ROOT / "platform" / "qz_entry.sh").read_text(encoding="utf-8")
        self.assertIn("run_release_python()", text)
        self.assertIn("env -u LD_LIBRARY_PATH", text)
        self.assertIn("run_release_python -m mls_lite_runner run-task", text)
        self.assertIn("run_release_python -m mls_lite_runner run-round", text)

    def test_submit_scripts_use_provider_qualified_model(self):
        for name in (
            "submit-api-smoke.ps1",
            "submit-run-task.ps1",
            "submit-run-round.ps1",
        ):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('openai/deepseek-v4-flash', text, name)
            self.assertIn('OPENAI_API_KEY', text, name)

    def test_shared_environment_has_pinned_host_requirements(self):
        entry = (ROOT / "platform" / "qz_entry.sh").read_text(encoding="utf-8")
        requirements = (ROOT / "manifests" / "host-requirements.txt").read_text(encoding="utf-8")
        self.assertIn('-r "$host_requirements"', entry)
        self.assertIn("litellm==1.93.0", requirements)
        self.assertIn("torch==2.5.1+cpu", requirements)
        self.assertIn("deap==1.4.1", requirements)
        self.assertIn("causal-learn @ git+", requirements)
        self.assertIn("upgrade-release", entry)
        self.assertIn("reconcile-state", entry)


if __name__ == "__main__":
    unittest.main()
