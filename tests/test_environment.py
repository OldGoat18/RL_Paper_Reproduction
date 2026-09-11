from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rl_repro_harness.environment import dependency_plan, inspect_environment, prepare_environment, install_missing
from rl_repro_harness.dependencies import parse_dependency
from rl_repro_harness.service import Workspace


class EnvironmentTests(unittest.TestCase):
    def test_editable_sources_survive_plan_inspection_and_install(self):
        target = 'git+https://github.com/vwxyzjn/cleanrl.git@004f8a086a892a2a180f4dd332b90d83a968aa7a#egg=cleanrl'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements.txt').write_text('-e ' + target + '\n')
            info = inspect_environment(root)
            self.assertIn('-e ' + target, info['missing_dependencies'])
            with patch('rl_repro_harness.environment._run', return_value={'returncode': 0, 'output': ''}) as run:
                install_missing(root, sys.executable, info['missing_dependencies'])
            self.assertEqual(run.call_args.args[0][-2:], ['--editable', target])
            with patch('rl_repro_harness.environment.install_missing', return_value={'returncode': 0, 'output': ''}):
                self.assertEqual(prepare_environment(root)['status'], 'ready')

    def test_source_formats_and_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / 'local package'
            local.mkdir()
            for text in ('-e "./local package"', '--editable=./local package', '--editable ./local package'):
                dep = parse_dependency(text, root)
                self.assertTrue(dep.source)
                self.assertEqual(dep.install_args, ['--editable', str(local)])
            for text in ('https://example.test/pkg.whl', 'git+https://example.test/pkg.git',
                         'pkg @ https://example.test/pkg.whl'):
                self.assertTrue(parse_dependency(text, root).source)
            (root / 'requirements.txt').write_text('absent-for-test; python_version < "1"\n')
            self.assertEqual(inspect_environment(root)['missing_dependencies'], [])

    def test_conda_pip_editables_and_requirements_includes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements.txt').write_text('-r "nested req.txt"\n-c constraints.txt\n')
            (root / 'nested req.txt').write_text('--index-url https://example.test/simple\n--only-binary=mujoco\nrequests>=2\n')
            (root / 'constraints.txt').write_text('requests<3\n')
            (root / 'environment.yml').write_text('dependencies:\n  - pip:\n    - "-e git+https://example.test/pkg.git#egg=pkg"\n')
            info = inspect_environment(root)
            self.assertEqual(info['constraints'], ['requests<3'])
            self.assertIn('-e git+https://example.test/pkg.git#egg=pkg', info['requirements'])
            with patch('rl_repro_harness.environment._run', return_value={'returncode': 0, 'output': ''}) as run:
                install_missing(root, sys.executable, ['requests>=2'], pip_options=info['pip_options'])
            command = run.call_args.args[0]
            self.assertIn('--index-url', command)
            self.assertIn('https://example.test/simple', command)
            self.assertIn('--only-binary', command)

    def test_invalid_requirement_has_source_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements.txt').write_text('# comment\n--unsupported-pip-option\n')
            with self.assertRaisesRegex(ValueError, r'requirements.txt:2: Unsupported pip directive'):
                dependency_plan(root)

    def test_setup_py_mpi_dependency_is_detected_and_native_import_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'setup.py').write_text("from setuptools import setup\nsetup(install_requires=['mpi4py>=1'])\n")
            info = inspect_environment(root)
            self.assertIn('mpi4py>=1', info['requirements'])
            self.assertIn('mpi4py>=1', info['requirements'])
            self.assertIsNotNone(info['native_check'])

    def test_environment_yml_requires_conda_and_preserves_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'environment.yml').write_text('name: demo\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.9\n  - mpi4py\n  - pip:\n    - requests>=2\n')
            plan = dependency_plan(root)
            self.assertEqual(plan['channels'], ['conda-forge'])
            self.assertIn('mpi4py', plan['conda'])
            self.assertIn('requests>=2', plan['requirements'])

    def test_prepare_environment_does_not_install_when_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = prepare_environment(root)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['steps'], [])

    def test_workspace_launch_records_environment_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / '.meta')
            try:
                project = workspace.register(root)
                ids = workspace.launch({'project_id': project['id'], 'command': [sys.executable, '-c', 'pass'], 'seeds': [None], 'repeats': 1, 'auto_install': False})
                self.assertEqual(len(ids), 1)
                while workspace.jobs:
                    pass
                record = workspace.records()[0]
                self.assertIn('environment_setup', record)
                self.assertIn('python', record['environment_setup'].get('inspection', record['environment_setup']))
            finally:
                workspace.close()

    def test_failed_dependency_installation_never_runs_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements.txt').write_text('package-that-cannot-exist-for-rl-harness==0\n')
            ran = root / 'ran'
            command = [sys.executable, '-c', f'from pathlib import Path; Path({str(ran)!r}).write_text("ran")']
            with patch('rl_repro_harness.environment.install_missing', return_value={'returncode': 1, 'output': 'install failed', 'command': [], 'attempted': True, 'installed': []}):
                result = prepare_environment(root, auto_install=True)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(ran.exists())
