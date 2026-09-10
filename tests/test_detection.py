import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rl_repro_harness.detector import collect_sources, detect_output_paths
from rl_repro_harness.llm import detect_with_llm
from rl_repro_harness.llm import load_config, save_config, validate_config


class DetectionTests(unittest.TestCase):
    def test_yaml_preserves_multiple_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text("outputs:\n  result_path: ./results/\n  checkpoint_path: ./checkpoints/\n  log_path: ./logs/\n")
            result = detect_output_paths(root)
            self.assertIsNone(result.detected_output_path)
            self.assertEqual({c.path for c in result.candidates}, {"./results", "./checkpoints", "./logs"})

    def test_command_takes_precedence_and_root_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"output_dir":"./old"}')
            self.assertEqual(detect_output_paths(root, ["python", "train.py", "--output=/"]).detected_output_path, "/")

    def test_does_not_read_output_or_tooling_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("results", ".codex", "experiments", "tests", "node_modules"):
                (root / name).mkdir()
                (root / name / "config.json").write_text('{"output_path":"wrong"}')
            original = Path.read_text
            def guarded(path, *args, **kwargs):
                self.assertEqual(path.parent, root, f"Read artifact: {path}")
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", guarded):
                self.assertEqual(collect_sources(root), {})

    def test_python_argparse_and_toml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "train.py").write_text("p.add_argument('--output', default='experiments')\n")
            (root / "config.toml").write_text('[outputs]\npath = "experiments"\n')
            result = detect_output_paths(root)
            self.assertEqual(result.detected_output_path, "experiments")
            self.assertEqual(result.confidence, "high")

    def test_templates_and_unrelated_output_keys_are_not_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"expected_output":"not/a/path","output_path":"runs/${seed}"}')
            result = detect_output_paths(root)
            self.assertIsNone(result.detected_output_path)
            self.assertEqual(len(result.candidates), 1)

    def test_llm_requires_verbatim_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("outputs are in ./native")
            response = {"choices": [{"message": {"content": json.dumps({"candidates": [
                {"path": "./invented", "source": "README.md", "evidence": "./invented", "confidence": "high"},
                {"path": "./native", "source": "README.md", "evidence": "outputs are in ./native", "confidence": "high"}
            ]})}}]}
            import io
            with patch.dict("os.environ", {"RL_HARNESS_LLM_URL": "http://localhost/llm", "RL_HARNESS_LLM_MODEL": "test"}), patch("rl_repro_harness.llm.urlopen", return_value=io.StringIO(json.dumps(response))):
                result = detect_with_llm(root)
            self.assertEqual(result.detected_output_path, "./native")
            self.assertEqual(len(result.candidates), 1)

    def test_llm_config_is_validated_and_key_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            config = save_config(directory, {"base_url": "https://example.test/v1", "model": "model-a", "api_key": "secret", "timeout_seconds": 10})
            self.assertTrue(config.public_dict()["api_key_configured"])
            self.assertNotIn("secret", json.dumps(config.public_dict()))
            self.assertEqual(load_config(directory).api_key, "secret")
            self.assertEqual(Path(directory, "llm.json").stat().st_mode & 0o077, 0)
            with self.assertRaises(ValueError):
                validate_config({"base_url": "file:///etc/passwd", "model": "x"})
