import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_fixture import SOURCE, entry, envelope
from rl_repro_harness.analysis import analyze_project, analyze_python_file, validate_plan
from rl_repro_harness.llm import validate_config
from rl_repro_harness.service import Workspace
from rl_repro_harness.training import decision, apply_decision


class AIExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / 'project'
        self.project_root.mkdir()
        (self.project_root / 'train.py').write_text(SOURCE)
        self.workspace = Workspace(self.root / 'metadata')
        self.project = self.workspace.register(self.project_root)
        self.config = validate_config({'base_url': 'http://127.0.0.1:9', 'model': 'fixture'})
        self.catalog = validate_plan(self.project_root, {'entries': [entry()], 'outputs': [], 'uncertainties': []}, {'train.py': SOURCE})
        self.project['analysis'] = {'catalog': self.catalog}
        self.entry = self.catalog['entries'][0]

    def tearDown(self):
        self.workspace.close()
        self.temp.cleanup()

    def payload(self, **extra):
        return {'project_id': self.project['id'], 'entry_id': self.entry['id'], 'command': self.entry['command'],
                'working_directory': str(self.project_root), 'python': sys.executable, 'steps': 20,
                'selections': {'algorithms': ['ppo'], 'environments': ['CartPole-v1'], 'seeds': [0]},
                'auto_install': False, 'start_immediately': False, **extra}

    def metadata(self):
        record = self.workspace.record(self.workspace.launch(self.payload())[0])
        from rl_repro_harness.metadata import ExecutionMetadata
        return ExecutionMetadata(**record)

    def wait(self, ids):
        deadline = time.monotonic() + 10
        while any(i in self.workspace.jobs for i in ids) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(any(i in self.workspace.jobs for i in ids))
        return [self.workspace.record(i) for i in ids]

    def test_ai_selects_sources_and_does_not_write_project(self):
        before = (self.project_root / 'train.py').read_bytes()
        with patch('rl_repro_harness.analysis._request', side_effect=[envelope({'read_files': ['train.py']}), envelope({'entries': [entry()], 'outputs': [], 'uncertainties': []})]) as request:
            result = analyze_project(self.project_root, self.config)
        self.assertEqual(result['algorithms'], ['ppo', 'trpo'])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(before, (self.project_root / 'train.py').read_bytes())
        self.assertEqual([p.name for p in self.project_root.iterdir()], ['train.py'])

    def test_single_file_supplement_has_exact_scope_and_keeps_command(self):
        (self.project_root / 'extras.py').write_text("supported = ['a2c']\n")
        response = {'additions': [{'entry_id': self.entry['id'], 'values': ['a2c'], 'environment_arguments': {},
                                  'evidence': [{'source': 'extras.py', 'quote': "supported = ['a2c']"}]}], 'entries': [], 'uncertainties': []}
        with patch('rl_repro_harness.analysis._request', return_value=envelope(response)) as request, patch('os.walk', side_effect=AssertionError('Whole-project scan')):
            result = analyze_python_file(self.project_root, self.config, 'extras.py', 'algorithms', self.catalog, self.entry['id'])
        context = json.loads(request.call_args.args[1][1]['content'])
        self.assertEqual(list(context['sources']), ['extras.py'])
        self.assertEqual(result['entries'][0]['algorithms'], ['a2c', 'ppo', 'trpo'])
        self.assertEqual(result['entries'][0]['command'], self.entry['command'])
        with patch('rl_repro_harness.analysis._request', return_value=envelope({'read_files': ['train.py']})):
            with self.assertRaisesRegex(ValueError, 'scope|schema'):
                analyze_python_file(self.project_root, self.config, 'extras.py', 'algorithms', self.catalog, self.entry['id'])

    def test_invented_values_commands_and_path_escape_rejected(self):
        for mutation in [lambda e: e['algorithms'].append('imaginary'), lambda e: e.update(command='python -c pass'), lambda e: e.update(path='../outside.py')]:
            candidate = entry(); mutation(candidate)
            with self.assertRaises(ValueError):
                validate_plan(self.project_root, {'entries': [candidate], 'outputs': [], 'uncertainties': []}, {'train.py': SOURCE})

    def test_matrix_and_skip_completed_on_every_start(self):
        data = self.payload(selections={'algorithms': ['ppo', 'trpo'], 'environments': ['CartPole-v1', 'MountainCar-v0'], 'seeds': [0, 1]})
        plan = self.workspace.command_plan(data)
        self.assertEqual(len(plan['executions']), 8)
        ids = self.workspace.launch(self.payload(start_immediately=True))
        first = self.wait(ids)[0]
        self.assertEqual(first['status'], 'success', first.get('error'))
        self.assertEqual(first['completed_steps'], 20)
        repeated = self.workspace.launch(self.payload())
        self.workspace.start_execution(repeated[0])
        skipped = self.wait(repeated)[0]
        self.assertEqual(skipped['status'], 'skipped_completed')
        self.assertEqual(skipped['training_decision']['remaining_steps'], 0)
        self.assertEqual(self.workspace.command_plan(self.payload())['completed_pairs'][0]['action'], 'skipped_completed')
        larger = self.workspace.launch(self.payload(steps=30, start_immediately=True))
        stopped = self.wait(larger)[0]
        self.assertEqual(stopped['status'], 'needs_attention')
        self.assertIn('checkpoint', stopped['error'])

    def test_parallel_duplicates_claim_training_identity(self):
        ids = self.workspace.launch(self.payload(start_immediately=True))
        ids += self.workspace.launch(self.payload(start_immediately=True))
        records = self.wait(ids)
        self.assertEqual(sorted(r['status'] for r in records), ['skipped_completed', 'success'])

    def test_native_resume_validates_checkpoint_and_preserves_files(self):
        metadata = self.metadata()
        checkpoint = self.project_root / 'native-checkpoint.json'
        checkpoint.write_text('{"weights":[1,2]}')
        # The project owns this status interface and its checkpoint validation implementation.
        (self.project_root / 'inspect.py').write_text("import json\nfrom pathlib import Path\nprint(Path('native-state.json').read_text())\n")
        metadata.training_contract['recovery'] = {'probe_command': ['python', 'inspect.py'], 'resume_flag': '--resume', 'step_mode': 'remaining',
            'success_means_target': False, 'fields': {'exists': 'exists', 'completed_steps': 'completed_steps', 'checkpoints': 'checkpoints'}}
        saved = {'path': str(checkpoint), 'completed_steps': 8, 'loadable': True, 'algorithm': metadata.algorithm,
                 'environment': metadata.rl_environment, 'seed': metadata.seed, 'config_key': metadata.configuration_key}
        report = {'exists': True, 'completed_steps': 8, 'checkpoints': [{**saved, 'loadable': False}, saved]}
        (self.project_root / 'native-state.json').write_text(json.dumps(report))
        before = checkpoint.read_bytes()
        result = decision(metadata, [])
        self.assertEqual(result['action'], 'resume')
        self.assertEqual(result['remaining_steps'], 12)
        apply_decision(metadata, result)
        self.assertIn(str(checkpoint), metadata.command)
        self.assertEqual(metadata.command[metadata.command.index('--steps') + 1], '12')
        self.assertEqual(checkpoint.read_bytes(), before)
        for changed in [{'loadable': False}, {'seed': 9}, {'algorithm': 'wrong'}, {'environment': 'other'}, {'config_key': 'wrong'}, {'completed_steps': 7}, {'path': str(checkpoint) + '.missing'}]:
            report['checkpoints'] = [{**saved, **changed}]
            (self.project_root / 'native-state.json').write_text(json.dumps(report))
            self.assertEqual(decision(metadata, [])['action'], 'needs_attention')
        report.update(completed_steps=25, checkpoints=[])
        (self.project_root / 'native-state.json').write_text(json.dumps(report))
        self.assertEqual(decision(metadata, [])['action'], 'skipped_completed')

    def test_changed_config_not_skipped_and_unknown_progress_not_inferred(self):
        old = self.metadata()
        previous = {**old.to_dict(), 'status': 'success', 'completed_steps': None}
        new = self.metadata()
        self.assertEqual(decision(new, [previous])['action'], 'needs_attention')
        old.completed_steps = 20
        previous.update(completed_steps=20)
        data = self.payload(command=self.entry['command'] + ' --learning-rate 0.02')
        records = self.workspace.launch(data)
        from rl_repro_harness.metadata import ExecutionMetadata
        changed = ExecutionMetadata(**self.workspace.record(records[0]))
        self.assertEqual(decision(changed, [previous])['action'], 'new_run')

    def test_resource_policy_and_gpu_admission(self):
        policy = {'max_parallel': 7, 'cpu_limit': 80, 'ram_reserve_mb': 1024, 'gpu_reserve_mb': 512}
        self.workspace.configure_resources(policy)
        self.assertEqual(json.loads((self.workspace.root / 'resource-policy.json').read_text()), policy)
        metadata = self.metadata()
        metadata.resource_request = {'cpu_cores': 2, 'ram_mb': 1024, 'gpu_memory_mb': 2048}
        snapshot = {'cpu_count': 8, 'memory_available_bytes': 4 * 1024**3, 'gpus': [{'id': '0', 'free_mb': 4096}]}
        self.assertEqual(self.workspace._allocation(metadata, snapshot)['gpu_id'], '0')
        snapshot['gpus'][0]['free_mb'] = 2100
        self.assertIsNone(self.workspace._allocation(metadata, snapshot))
        with self.assertRaises(ValueError):
            self.workspace.configure_resources({**policy, 'max_parallel': 0})
