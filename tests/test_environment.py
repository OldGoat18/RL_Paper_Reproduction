from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rl_repro_harness.environment import dependency_plan, inspect_environment, prepare_environment
from rl_repro_harness.service import Workspace


class EnvironmentTests(unittest.TestCase):
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
