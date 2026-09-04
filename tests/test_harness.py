from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rl_repro_harness.detector import detect_output_paths
from rl_repro_harness.runner import run_project


class HarnessTests(unittest.TestCase):
    def test_structured_output_path_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text("outputs:\n  path: ./experiments/\n", encoding="utf-8")
            result = detect_output_paths(root)
            self.assertEqual(result.detected_output_path, "./experiments")
            self.assertEqual(result.confidence, "high")

    def test_ambiguous_paths_are_marked_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("output_path: ./runs/\nresult_path: ./results/\n", encoding="utf-8")
            result = detect_output_paths(root)
            self.assertIsNone(result.detected_output_path)
            self.assertEqual(result.confidence, "uncertain")

    def test_runner_records_metadata_without_copying_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps({"outputs": {"path": "./native-results"}}), encoding="utf-8")
            metadata, detection, code = run_project(root, ["python", "-c", "from pathlib import Path; Path('native-results').mkdir()"], metadata_root=root / ".meta")
            self.assertEqual(code, 0)
            self.assertEqual(metadata.status, "success")
            self.assertEqual(detection.detected_output_path, "./native-results")
            self.assertTrue((root / "native-results").is_dir())
            records = list((root / ".meta" / "executions").glob("*.json"))
            self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
