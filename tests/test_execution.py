from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from rl_repro_harness.runner import run_project
from rl_repro_harness.service import Workspace


class ExecutionTests(unittest.TestCase):
    def test_missing_executable_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, _, code = run_project(root, [str(root / "nonexistent")], metadata_root=root / ".meta")
            self.assertEqual(code, 127)
            self.assertEqual(metadata.status, "failed")
            self.assertTrue(metadata.error)
            persisted = json.loads((root / ".meta" / "executions" / f"{metadata.execution_id}.json").read_text())
            self.assertIsNotNone(persisted["end_time"])

    def test_process_receives_effective_environment_and_native_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [sys.executable, "-c", "import os,sys; from pathlib import Path; assert os.environ['CUDA_VISIBLE_DEVICES']=='7'; assert Path.cwd()==Path(sys.argv[1]); sys.exit(3)", str(root)]
            metadata, _, code = run_project(root, command, env={"CUDA_VISIBLE_DEVICES": "7"}, metadata_root=root / ".meta", output_override="native")
            self.assertEqual(code, 3)
            self.assertEqual(metadata.environment["CUDA_VISIBLE_DEVICES"], "7")
            self.assertEqual(metadata.output_path_confidence, "override")
            self.assertFalse((root / "native").exists())

    def test_cancellation_finishes_record(self):
        with tempfile.TemporaryDirectory() as directory:
            event = threading.Event()
            timer = threading.Timer(0.3, event.set)
            timer.start()
            try:
                metadata, _, code = run_project(directory, [sys.executable, "-c", "import time; time.sleep(20)"], metadata_root=Path(directory) / ".meta", cancel=event)
            finally:
                timer.join()
            self.assertEqual(code, 130)
            self.assertEqual(metadata.status, "cancelled")
            self.assertIsNotNone(metadata.end_time)

    def test_multiseed_queue_substitutes_exact_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / ".meta")
            try:
                project = workspace.register(root)
                ids = workspace.launch({"project_id": project["id"], "command": [sys.executable, "-c", "import sys; from pathlib import Path; Path(sys.argv[1]).write_text(sys.argv[2])", "seed-{seed}-{repeat}.txt", "{seed}"], "seeds": [2, 3], "repeats": 2})
                deadline = time.monotonic() + 10
                while workspace.jobs and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(workspace.jobs)
                self.assertEqual(len(ids), 4)
                self.assertTrue(all(r["status"] == "success" for r in workspace.records()))
                self.assertEqual((root / "seed-3-2.txt").read_text(), "3")
                with self.assertRaisesRegex(ValueError, "contain.*seed"):
                    workspace.launch({"project_id": project["id"], "command": [sys.executable, "-c", "pass"], "seeds": [1, 2]})
            finally:
                workspace.close()

    def test_second_supervisor_cannot_modify_live_records(self):
        import os
        if os.name != "posix":
            self.skipTest("POSIX file lock")
        with tempfile.TemporaryDirectory() as directory:
            first = Workspace(directory)
            try:
                with self.assertRaisesRegex(ValueError, "Another Web server"):
                    Workspace(directory)
            finally:
                first.close()

    def test_catalog_lists_algorithms_environments_and_runnable_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "train.py").write_text("import gym\nparser.add_argument('--alg'); env='SafetyBallCircle-v0'\nif __name__ == '__main__': pass\n")
            workspace = Workspace(root / ".meta")
            try:
                project = workspace.register(root)
                catalog = workspace.catalog(project["id"])
                self.assertIn("train.py", [entry["path"] for entry in catalog["entries"]])
                self.assertIn("SafetyBallCircle-v0", catalog["environments"])
                self.assertIn("train", catalog["entries"][0]["path"])
            finally:
                workspace.close()

    def test_held_execution_starts_later_and_does_not_block_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / ".meta")
            try:
                project = workspace.register(root)
                ids = workspace.launch({
                    "project_id": project["id"],
                    "command": ["/bin/sh", "-c", "printf ok > started.txt"],
                    "seeds": [None],
                    "repeats": 1,
                    "start_immediately": False,
                    "auto_install": False,
                })
                self.assertEqual(len(ids), 1)
                self.assertFalse(workspace.jobs)
                self.assertEqual(workspace.record(ids[0])["status"], "held")
                workspace.start_execution(ids[0])
                deadline = time.monotonic() + 5
                while workspace.record(ids[0])["status"] not in {"success", "failed", "cancelled"} and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(workspace.record(ids[0])["status"], "success")
                self.assertEqual((root / "started.txt").read_text(), "ok")
            finally:
                workspace.close()
