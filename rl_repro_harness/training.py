"""Training identity and decisions over metadata and a project's native probe."""
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .commands import replace_option


def configuration_key(metadata, entry):
    command = list(metadata.command)
    if entry.get('steps_flag'):
        command = replace_option(command, (entry['steps_flag'],), entry['steps_flag'], None)
    recovery = entry.get('recovery') or {}
    if recovery.get('resume_flag'):
        command = replace_option(command, (recovery['resume_flag'],), recovery['resume_flag'], None)
    identity = {'project': metadata.project, 'git_commit': metadata.git_commit, 'command': command,
                'algorithm': metadata.algorithm, 'environment': metadata.rl_environment, 'seed': metadata.seed,
                'task': metadata.task, 'directory': metadata.working_directory, 'env': metadata.launch_environment,
                'runtime': entry.get('runtime', {})}
    sources = [Path(metadata.project) / entry['path']] if entry.get('path') else []
    for arg in command[1:]:
        path = Path(metadata.working_directory) / arg.partition('=')[-1]
        if path.is_file() and path.suffix in {'.py', '.json', '.yaml', '.yml', '.toml', '.cfg'}:
            sources.append(path)
    identity['configuration_files'] = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def decision(metadata, records, *, probe=True):
    result = {'action': 'new_run', 'completed_steps': 0, 'target_steps': metadata.training_steps,
              'remaining_steps': metadata.training_steps, 'checkpoint': None, 'message': 'No compatible training recorded'}
    if metadata.task != 'train' or not metadata.training_steps:
        return result
    history = [r for r in records if r['execution_id'] != metadata.execution_id and r.get('configuration_key') == metadata.configuration_key
               and r.get('status') not in {'held', 'queued', 'skipped_completed', 'needs_attention'}]
    legacy = [r for r in records if not r.get('configuration_key') and r.get('project') == metadata.project
              and r.get('algorithm') == metadata.algorithm and r.get('rl_environment') == metadata.rl_environment and r.get('seed') == metadata.seed
              and r.get('task') == metadata.task and r.get('status') not in {'held', 'queued'}]
    if legacy:
        return {**result, 'action': 'needs_attention', 'message': 'Legacy training exists without a verifiable training configuration or completed-step count'}
    completed = max((r.get('completed_steps') or 0 for r in history), default=0)
    if completed >= metadata.training_steps:
        return {**result, 'action': 'skipped_completed', 'completed_steps': completed, 'remaining_steps': 0,
                'message': f'Existing training completed {completed} steps; target is {metadata.training_steps}'}
    recovery = metadata.training_contract.get('recovery')
    if not probe:
        return {**result, 'completed_steps': completed, 'remaining_steps': metadata.training_steps - completed,
                'action': 'needs_attention' if history else 'new_run',
                'message': 'Existing training requires native checkpoint validation at start' if history else result['message']}
    if not recovery:
        if history:
            return {**result, 'action': 'needs_attention', 'completed_steps': completed,
                    'remaining_steps': metadata.training_steps - completed,
                    'message': 'Existing training has no verified native progress/checkpoint validation interface; refusing to restart or overwrite it'}
        # An existing output location may contain runs created outside Harness. Do not guess their semantics.
        if metadata.detected_output_path:
            output = Path(metadata.detected_output_path).expanduser()
            output = output if output.is_absolute() else Path(metadata.working_directory) / output
            if output.exists() and (not output.is_dir() or next(output.iterdir(), None) is not None):
                return {**result, 'action': 'needs_attention', 'message': 'Output location already contains project data, but no native progress/checkpoint validation interface is available'}
        return result
    substitutions = {'algorithm': metadata.algorithm, 'env': metadata.rl_environment, 'seed': metadata.seed,
                     'target_steps': metadata.training_steps, 'config_key': metadata.configuration_key,
                     'output': metadata.detected_output_path or ''}
    argv = []
    for arg in recovery['probe_command']:
        for key, value in substitutions.items():
            arg = arg.replace('{' + key + '}', str(value))
        argv.append(arg)
    argv[0] = metadata.command[0]
    try:
        # Bound captured protocol data. No checkpoint data is copied or deserialized by Harness.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(argv, cwd=metadata.working_directory, env={**os.environ, **metadata.launch_environment},
                                       stdout=stdout, stderr=stderr)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
                raise ValueError('Native checkpoint probe timed out')
            if process.returncode:
                raise ValueError('Native checkpoint probe failed with exit code ' + str(process.returncode))
            stdout.seek(0)
            raw = stdout.read(262145)
            if len(raw) > 262144:
                raise ValueError('Native checkpoint response exceeds limit')
            report = json.loads(raw)
        fields = recovery['fields']
        exists, completed, checkpoints = (report[fields[key]] for key in ('exists', 'completed_steps', 'checkpoints'))
        if type(exists) is not bool or type(completed) is not int or completed < 0 or not isinstance(checkpoints, list):
            raise ValueError('Invalid native training state')
        if not exists:
            if history or completed:
                raise ValueError('Native probe could not locate the existing compatible training')
            return result
        result.update(completed_steps=completed, remaining_steps=max(0, metadata.training_steps - completed))
        if completed >= metadata.training_steps:
            return {**result, 'action': 'skipped_completed', 'message': f'Existing training completed {completed} steps; target is {metadata.training_steps}'}
        # The project orders checkpoints newest-first and verifies loading and saved configuration.
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict) or checkpoint.get('loadable') is not True:
                continue
            expected = {'algorithm': metadata.algorithm, 'environment': metadata.rl_environment,
                        'seed': metadata.seed, 'config_key': metadata.configuration_key}
            if any(checkpoint.get(k) != v for k, v in expected.items()):
                continue
            if type(checkpoint.get('completed_steps')) is not int or checkpoint['completed_steps'] != completed:
                continue
            path = Path(checkpoint['path']).expanduser()
            path = path if path.is_absolute() else Path(metadata.working_directory) / path
            if not path.is_file() or path.stat().st_size == 0:
                continue
            return {**result, 'action': 'resume', 'checkpoint': str(path.resolve()),
                    'message': f'Resume validated checkpoint at {completed} steps; {result["remaining_steps"]} steps remaining'}
        raise ValueError('Incomplete training has no loadable, compatible checkpoint at its recorded progress')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**result, 'action': 'needs_attention', 'message': str(exc)}


def apply_decision(metadata, result):
    metadata.training_action = result['action']
    metadata.training_decision = result
    metadata.completed_steps = result['completed_steps']
    metadata.remaining_steps = result['remaining_steps']
    metadata.checkpoint = result['checkpoint']
    if result['action'] == 'resume':
        contract = metadata.training_contract
        recovery = contract['recovery']
        metadata.command = replace_option(metadata.command, (recovery['resume_flag'],), recovery['resume_flag'], result['checkpoint'])
        if recovery['step_mode'] == 'remaining':
            flag = contract['steps_flag']
            metadata.command = replace_option(metadata.command, (flag,), flag, str(result['remaining_steps']))
    elif result['action'] in {'skipped_completed', 'needs_attention'}:
        metadata.status = result['action']
        metadata.error = result['message'] if result['action'] == 'needs_attention' else None
