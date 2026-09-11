import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rl_repro_harness.discovery import discover, argument_contract
from rl_repro_harness.commands import build_commands
from rl_repro_harness.service import Workspace


class ProjectAnalysisTests(unittest.TestCase):
    def fixture(self, root):
        child = root / "external" / "FlowRL"
        child.mkdir(parents=True)
        (child / "README.md").write_text("python3 main.py --domain dog --task run\n")
        (child / "main.py").write_text(
            "if __name__ == '__main__':\n"
            " arg.add_arg('domain', 'dog')\n"
            " arg.add_arg('task', 'run')\n"
            " arg.add_arg('algo', 'flowac')\n"
            " arg.add_arg('seed', 0)\n"
            " arg.add_arg('num_steps', 2000001)\n"
        )
        (child / "requirements .txt").write_text("-e git+https://example.test/repo.git#egg=demo\n")
        (child / "scripts").mkdir()
        (child / "scripts" / "train.sh").write_text("tasks=(run walk stand trot)\n")
        return child

    def test_composite_analysis_is_bounded_and_isolates_bad_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = self.fixture(root)
            bad = root / "external" / "other"
            bad.mkdir()
            (bad / "requirements.txt").write_text("--unknown-option\n")
            output = root / "results"
            output.mkdir()
            (output / "README.md").write_text("output owned by project")
            (output / "main.py").write_text("if __name__ == '__main__': pass")
            with patch('subprocess.Popen', side_effect=AssertionError("Analysis executed a process")):
                result = discover(root, include_dependencies=True)
            flow = next(e for e in result["entries"] if e["project_name"] == "FlowRL")
            self.assertEqual(flow["steps_flag"], "--num_steps")
            self.assertEqual(flow["algorithms"], ["flowac"])
            self.assertEqual(flow["environments"], ["dog/run", "dog/stand", "dog/trot", "dog/walk"])
            self.assertNotIn("results", [s["name"] for s in result["subprojects"]])
            self.assertEqual(next(s for s in result["subprojects"] if s["name"] == "other")["dependency_status"], "unsupported")
            self.assertEqual(next(s for s in result["subprojects"] if s["name"] == "FlowRL")["dependencies"]["dependency_files"], ["requirements .txt"])

    def test_web_analysis_plan_defaults_and_completed_pair_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = self.fixture(root)
            workspace = Workspace(root / ".metadata")
            try:
                with patch.object(workspace, 'prepare_project_environment'):
                    project = workspace.register(root)
                result = workspace.analyze(project["id"])
                entry = next(e for e in result["analysis"]["catalog"]["entries"] if e["project_name"] == "FlowRL")
                defaults = workspace.defaults(project["id"])
                self.assertEqual(len(defaults["seeds"].split(",")), 30)
                self.assertEqual(defaults["steps"], 5000000)
                data = {"project_id":project["id"], "entry_id":entry["id"], "command":entry["command"],
                        "python":sys.executable, "working_directory":str(child), "steps":5000000,
                        "selections":{"algorithms":["flowac"],"environments":["dog/run","dog/walk"],"seeds":list(range(30))},
                        "start_immediately":False}
                plan = workspace.command_plan(data)
                self.assertEqual(len(plan["executions"]), 60)
                command = plan["executions"][-1]["command"]
                self.assertEqual(command[command.index("--seed")+1], "29")
                self.assertEqual(command[command.index("--task")+1], "walk")
                self.assertEqual(command[command.index("--num_steps")+1], "5000000")
                self.assertNotIn("--env", command)
                self.assertEqual(plan["completed_pairs"], [])
                data["selections"]["seeds"] = [0]
                ids = workspace.launch(data)
                record = workspace.record(ids[0])
                record.update(status="success",exit_code=0)
                workspace._write(workspace.root / "executions" / (ids[0]+".json"), record)
                repeated = workspace.command_plan(data)
                self.assertEqual(repeated["completed_pairs"][0]["algorithm"], "flowac")
                self.assertEqual(repeated["completed_pairs"][0]["steps"], 5000000)
            finally:
                workspace.close()

    def test_steps_replace_existing_and_unknown_flag_is_not_invented(self):
        data = {"command":"python train.py --total-timesteps=10 --seed 0", "steps":5000000,
                "selections":{"seeds":list(range(30))}}
        plan = build_commands(data)
        self.assertEqual(len(plan["executions"]), 30)
        self.assertIn("5000000", plan["executions"][0]["command"])
        self.assertNotIn("--total-timesteps=10",plan["executions"][0]["command"])
        data["command"] = "python train.py"
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            build_commands(data)

    def test_tyro_dataclass_contract(self):
        flags, defaults, _ = argument_contract("class Args:\n seed: int = 1\n total_timesteps: int = 10000000\n env_id: str = 'CartPole-v1'\nargs = tyro.cli(Args)\n")
        self.assertIn('--total-timesteps', flags)
        self.assertEqual(defaults['--total-timesteps'], '10000000')
        self.assertEqual(defaults['--env-id'], 'CartPole-v1')
