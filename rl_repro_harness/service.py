"""Local project registry and execution queue. Stores metadata only."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import uuid
import ast
import re
import shutil
import time
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .detector import detect_output_paths
from .commands import build_commands
from .metadata import ExecutionMetadata
from .analysis import analyze_project, analyze_python_file, validate_command, LIMITS
from .training import configuration_key, decision, apply_decision
from .llm import detect_with_llm, load_config, save_config, test_connection, validate_config
from .runner import _now, _git_commit, execute_execution, prepare_execution
from .environment import conda_environments, current_python, dependency_root, inspect_environment, prepare_environment, python_path, MANIFESTS


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.instance_id = uuid.uuid4().hex
        self.lockfile = (self.root / "web.lock").open("a+")
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self.lockfile.close()
                raise ValueError("Another Web server is using this metadata directory") from exc
        self.lock = threading.RLock()
        self.max_parallel = max(1, min(4, (os.cpu_count() or 2) // 2))
        self.policy = {'max_parallel': self.max_parallel, 'cpu_limit': 90, 'ram_reserve_mb': 1024, 'gpu_reserve_mb': 512}
        policy_file = self.root / 'resource-policy.json'
        if policy_file.exists():
            self.policy.update(json.loads(policy_file.read_text()))
            self.max_parallel = self.policy['max_parallel']
        self.pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="rl-execution")
        self.environment_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rl-environment")
        self.jobs = {}
        self.environment_jobs = {}
        self.processes = {}
        self.live_metadata = {}
        self.held = set()
        self.held_events = {}
        self.reserved_slots = 0
        self.training_claims = set()
        self.allocations = {}
        self.gpu_snapshot = []
        self.gpu_snapshot_time = 0
        self.python_environments = conda_environments()
        self.registry = self.root / "projects.json"
        self.projects = json.loads(self.registry.read_text()) if self.registry.exists() else []
        for path in (self.root / "executions").glob("*.json"):
            try:
                record = json.loads(path.read_text())
                if record.get("supervisor_id") and record.get("status") in {"queued", "preparing", "running", "paused"}:
                    record.update(status="unknown", error="Previous supervisor stopped; process state is unknown")
                    self._write(path, record)
            except (OSError, ValueError):
                continue

    def _write(self, path, value):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def register(self, path, name=None):
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("Project directory does not exist")
        with self.lock:
            existing = next((p for p in self.projects if p["path"] == str(path)), None)
            if existing:
                return existing
            project = {"id": uuid.uuid4().hex, "name": (name or path.name)[:120], "path": str(path), "created_at": _now(), "detection": None}
            manifest = path / "harness.project.json"
            if manifest.is_file():
                spec = json.loads(manifest.read_text(encoding="utf-8"))
                project["presets"] = spec.get("tasks", {})
                project["name"] = name or spec.get("name", path.name)
            self.projects.append(project)
            self._write(self.registry, self.projects)
            return project

    def project(self, project_id):
        with self.lock:
            result = next((p for p in self.projects if p["id"] == project_id), None)
        if not result:
            raise ValueError("Project not found")
        return result

    def catalog(self, project_id):
        project = self.project(project_id)
        cached = project.get('analysis', {}).get('catalog')
        return cached if cached and cached.get('origin') == 'llm' else {'entries': [], 'algorithms': [], 'environments': [], 'subprojects': [], 'project_id': project_id, 'origin': 'pending_ai'}

    def defaults(self, project_id):
        """Return only defaults backed by project-authored evidence."""
        project = self.project(project_id)
        catalog = self.catalog(project_id)
        preset = project.get("presets", {}).get("train") if isinstance(project.get("presets"), dict) else None
        entry = next((e for e in catalog["entries"] if e.get("source", "").lower().startswith("readme") and e.get("confidence") == "high"), None)
        entry = entry or next((e for e in catalog["entries"] if e.get("confidence") == "high"), None)
        command = preset or (entry.get("command") if entry else None)
        directory = project["path"]
        if entry and entry.get("directory") and not preset:
            directory = str(Path(project["path"]) / entry["directory"])
        environment = (entry.get("environments") or [None])[0] if entry else None
        seed_default = (entry.get("defaults") or {}).get("--seed", "") if entry else ""
        return {"project_id": project_id, "task": "train", "command": command.replace("{python}", sys.executable) if command else "", "working_directory": directory, "environment": environment or "", "seeds": ", ".join(map(str, range(30))), "steps": 5000000, "entry_id": entry["id"] if entry else None, "repeats": "1", "output": (project.get('detection') or {}).get('detected_output_path') or "", "source": "project preset" if preset else (entry.get("source") if entry else None), "confidence": "high" if command else "uncertain"}

    def analyze(self, project_id, command="", llm=False):
        project = self.project(project_id)
        config = load_config(self.root)
        if config is None:
            raise ValueError("Configure an LLM provider before project analysis")
        catalog = {**analyze_project(project['path'], config), 'project_id': project_id}
        outputs = catalog['outputs']
        paths = {item['path'] for item in outputs if item['confidence'] != 'uncertain'}
        path = next(iter(paths)) if len(paths) == 1 and all(i['confidence'] != 'uncertain' for i in outputs) else None
        result = {'project_path': project['path'], 'candidates': outputs, 'detected_output_path': path,
                  'confidence': 'high' if path else 'uncertain', 'method': 'llm'}
        with self.lock:
            project['analysis'] = {'catalog': catalog, 'limits': LIMITS, 'analyzed_at': _now()}
            project["detection"] = result
            self._write(self.registry, self.projects)
        return {**result, "analysis": project["analysis"]}

    def analyze_file(self, project_id, path, kind, entry_id):
        if kind not in {'algorithms', 'environments'}:
            raise ValueError('Choose algorithms or environments')
        project = self.project(project_id)
        config = load_config(self.root)
        if not config:
            raise ValueError('Configure an LLM provider first')
        catalog = self.catalog(project_id)
        existing = next((e for e in catalog['entries'] if e['id'] == entry_id), None)
        result = analyze_python_file(project['path'], config, path, kind, catalog, entry_id)
        with self.lock:
            current = self.catalog(project_id)
            entries = {e['id']: e for e in current['entries']}
            added = []
            for entry in result['entries']:
                prior = entries.get(entry['id'])
                if prior:
                    # Supplemental evidence may extend choices, but cannot silently rewrite the original command.
                    for key in ('command', 'directory', 'bindings', 'steps_flag', 'runtime', 'recovery'):
                        if entry[key] != prior[key]:
                            raise ValueError('Supplemental analysis changed the existing execution contract; run full project analysis')
                    for key in ('algorithms', 'environments', 'flags'):
                        entry[key] = sorted(set(prior[key] + entry[key]))
                    entry['environment_arguments'] = {**prior['environment_arguments'], **entry['environment_arguments']}
                    entry['source_hashes'] = {**prior['source_hashes'], **entry['source_hashes']}
                entries[entry['id']] = entry
                added.extend(entry[kind])
            updated = {**current, 'origin': 'llm', 'entries': list(entries.values())}
            for key in ('algorithms', 'environments'):
                updated[key] = sorted({v for e in entries.values() for v in e[key]})
            project['analysis'] = {**project.get('analysis', {}), 'catalog': updated, 'analyzed_at': _now(), 'limits': LIMITS}
            self._write(self.registry, self.projects)
        return {'catalog': updated, 'added': sorted(set(added)), 'entry_ids': [e['id'] for e in result['entries']], 'uncertainties': result['uncertainties']}

    def command_plan(self, data):
        payload = dict(data)
        if data.get('entry_id'):
            entry = next((e for e in self.catalog(data['project_id'])['entries'] if e['id'] == data['entry_id']), None)
            if entry is None:
                raise ValueError('Entry point not found; analyze the project again')
            for key in ('algorithms', 'environments'):
                if any(value not in entry[key] for value in data.get('selections', {}).get(key, [])):
                    raise ValueError('Unsupported ' + key + ' for this entry point')
            payload.update({key: entry[key] for key in ('bindings', 'environment_arguments', 'steps_flag', 'flags')})
            payload['fixed_environment'] = len(entry['environments']) == 1 and not entry['bindings']['environments']
            root = Path(self.project(data['project_id'])['path'])
            directory = self._working_directory(self.project(data['project_id']), data.get('working_directory'))
            if directory != (root / entry['directory']).resolve():
                raise ValueError('Working directory must match the AI execution plan')
            argv = validate_command(root, directory, data['command'], root / entry['path'])
            allowed = set(entry['flags'])
            for arg in argv[2:]:
                if arg.startswith('--') and arg.partition('=')[0] not in allowed:
                    raise ValueError('Command contains an argument outside the AI execution plan: ' + arg)
            if entry['environments'] and not data.get('selections', {}).get('environments'):
                raise ValueError('Select at least one environment')
            if entry['algorithms'] and not data.get('selections', {}).get('algorithms'):
                raise ValueError('Select at least one algorithm')
        elif data.get('selections'):
            raise ValueError('Select an AI-analyzed entry point first')
        plan = build_commands(payload)
        project = self.project(data['project_id'])
        directory = str(self._working_directory(project, data.get('working_directory')))
        history = self.records()
        commit = _git_commit(Path(project['path']))
        completed = []
        for execution in plan['executions']:
            metadata = self._plan_metadata(project, execution, data, entry if data.get('entry_id') else {}, commit)
            check = decision(metadata, history, probe=False)
            execution['training_decision'] = check
            if check['action'] != 'new_run':
                completed.append({'algorithm': execution['algorithm'], 'environment': execution['rl_environment'],
                                  'seed': execution['seed'], 'steps': check['completed_steps'], 'action': check['action'], 'message': check['message']})
        plan['completed_pairs'] = completed
        return plan

    def _plan_metadata(self, project, execution, data, contract, commit):
        detection = project.get('detection') or {}
        metadata = ExecutionMetadata(execution_id=uuid.uuid4().hex, project=project['path'], git_commit=commit,
            task=data.get('task', 'train'), environment={}, command=list(execution['command']), seed=execution['seed'],
            repeat=execution['repeat'], start_time=_now(), end_time=None, exit_code=None, status='queued',
            detected_output_path=execution.get('output') or detection.get('detected_output_path'),
            output_path_confidence='override' if execution.get('output') else detection.get('confidence', 'uncertain'),
            output_candidates=detection.get('candidates', []), working_directory=str(self._working_directory(project, data.get('working_directory'))),
            algorithm=execution.get('algorithm'), rl_environment=execution.get('rl_environment'), training_steps=execution.get('steps'),
            training_contract=contract, launch_environment=dict(data.get('environment') or {}), resource_request=contract.get('resources', {}))
        if data.get('python'):
            metadata.command[0] = python_path(data['python'])
        metadata.configuration_key = configuration_key(metadata, contract)
        return metadata

    def records(self):
        records = []
        for path in (self.root / "executions").glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(record, dict) and "execution_id" in record:
                    records.append(record)
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda r: r.get("start_time") or "", reverse=True)

    def delete_records(self, execution_ids):
        if not isinstance(execution_ids, list) or not execution_ids:
            raise ValueError('Select at least one execution record')
        deleted, errors = [], []
        for execution_id in execution_ids:
            try:
                self.delete_record(execution_id)
                deleted.append(execution_id)
            except ValueError as exc:
                errors.append({'execution_id': execution_id, 'error': str(exc)})
        return {'deleted': deleted, 'errors': errors}

    def record(self, execution_id):
        if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ValueError("Invalid execution id")
        path = self.root / "executions" / f"{execution_id}.json"
        if not path.is_file():
            raise ValueError("Execution not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_record(self, execution_id):
        with self.lock:
            record = self.record(execution_id)
            if record.get("status") in {"queued", "preparing", "running", "paused"} or execution_id in self.jobs:
                raise ValueError("Active executions cannot be deleted")
            self.held_events.pop(execution_id, None)
            self.held.discard(execution_id)
            (self.root / "executions" / f"{execution_id}.json").unlink(missing_ok=True)
        return {"deleted": execution_id}

    def state(self):
        try:
            config = load_config(self.root)
            config_error = None
        except ValueError:
            config, config_error = None, "Stored AI configuration is invalid"
        return {"projects": self.projects, "executions": self.records(), "llm_available": config is not None, "llm": config.public_dict() if config else None, "llm_error": config_error, "python": current_python(), "python_environments": self.python_environments, "metadata_root": str(self.root), "working_directory": str(Path.cwd()), "resources": self.resources()}

    def resources(self):
        if time.monotonic() - self.gpu_snapshot_time > 5:
            self.gpu_snapshot_time = time.monotonic()
            try:
                import csv
                result = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.free,memory.total,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2)
                self.gpu_snapshot = [{'id': row[0].strip(), 'name': row[1].strip(), 'free_mb': int(row[2]), 'total_mb': int(row[3]), 'utilization': int(row[4])} for row in csv.reader(result.stdout.splitlines())] if result.returncode == 0 else []
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                self.gpu_snapshot = []
        extra = {'gpus': self.gpu_snapshot, 'policy': dict(self.policy)}
        try:
            import psutil
            memory = psutil.virtual_memory()
            return {**extra, 'cpu_percent': psutil.cpu_percent(interval=None), 'cpu_count': psutil.cpu_count() or 1,
                    'memory_percent': memory.percent, 'memory_available_bytes': memory.available,
                    'memory_total_bytes': memory.total, 'parallel_limit': self.max_parallel,
                    'running': self.reserved_slots, 'active': len(self.jobs) + len(self.held)}
        except ImportError:
            load = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
            memory = {}
            try:
                for line in Path('/proc/meminfo').read_text().splitlines():
                    key, value = line.split(':', 1)
                    memory[key] = int(value.split()[0]) * 1024
            except (OSError, ValueError):
                pass
            total, available = memory.get('MemTotal'), memory.get('MemAvailable')
            return {**extra, 'cpu_percent': round(load * 100 / max(os.cpu_count() or 1, 1), 1), 'cpu_count': os.cpu_count() or 1,
                    'memory_percent': 100 * (1 - available / total) if total and available else None, 'memory_available_bytes': available, 'memory_total_bytes': total,
                    'parallel_limit': self.max_parallel, 'running': self.reserved_slots, 'active': len(self.jobs) + len(self.held)}

    def configure_resources(self, data):
        bounds = {'max_parallel': (1, 32), 'cpu_limit': (1, 100), 'ram_reserve_mb': (0, 1048576), 'gpu_reserve_mb': (0, 1048576)}
        if set(data) != set(bounds) or any(type(data[k]) is not int or not low <= data[k] <= high for k, (low, high) in bounds.items()):
            raise ValueError('Invalid resource policy; parallel limit must be 1..32 and CPU limit 1..100')
        with self.lock:
            self.policy = dict(data)
            self.max_parallel = data['max_parallel']
            self._write(self.root / 'resource-policy.json', self.policy)
        return self.resources()

    def _allocation(self, metadata, snapshot):
        request = {'cpu_cores': 1, 'ram_mb': 512, 'gpu_memory_mb': 0, **metadata.resource_request}
        used_cpu = sum(r['cpu_cores'] for r in self.allocations.values())
        if used_cpu + request['cpu_cores'] > snapshot['cpu_count']:
            return None
        available = snapshot.get('memory_available_bytes')
        reserved_ram = sum(r['ram_mb'] for r in self.allocations.values())
        if available is None or available / 1048576 < self.policy['ram_reserve_mb'] + reserved_ram + request['ram_mb']:
            return None
        if request['gpu_memory_mb']:
            visible = metadata.launch_environment.get('CUDA_VISIBLE_DEVICES', os.environ.get('CUDA_VISIBLE_DEVICES'))
            for gpu in snapshot.get('gpus', []):
                if visible is not None and gpu['id'] not in visible.split(','):
                    continue
                reserved_gpu = sum(r['gpu_memory_mb'] for r in self.allocations.values() if r.get('gpu_id') == gpu['id'])
                if gpu['free_mb'] - reserved_gpu >= request['gpu_memory_mb'] + self.policy['gpu_reserve_mb']:
                    return {**request, 'gpu_id': gpu['id']}
            return None
        return request

    def _validate_runtime(self, runtime, info, python, root, env, auto_install, event):
        from packaging.specifiers import SpecifierSet
        from .environment import _run, install_missing
        if runtime['python_specifier'] and not SpecifierSet(runtime['python_specifier']).contains(info['python_version']):
            raise ValueError('AI plan requires Python ' + runtime['python_specifier'] + '; selected ' + info['python_version'])
        if runtime['requirements']:
            # Inspect installed versions without importing project modules.
            script = "import importlib.metadata as m,json,sys; print(json.dumps({r:m.version(r) if any(d.metadata['Name'].lower()==r.lower() for d in m.distributions()) else None for r in json.loads(sys.argv[1])}))"
            from packaging.requirements import Requirement
            requirements = [Requirement(r) for r in runtime['requirements']]
            result = _run([python, '-c', script, json.dumps([r.name for r in requirements])], root, env=env)
            if result['returncode']:
                raise ValueError('Cannot verify AI runtime constraints')
            versions = json.loads(result['output'])
            missing = [str(r) for r in requirements if not versions.get(r.name) or not r.specifier.contains(versions[r.name])]
            if missing and not auto_install:
                raise ValueError('AI runtime requirements are not satisfied: ' + ', '.join(missing))
            if missing:
                report = install_missing(root, python, missing, cancel=event, env=env)
                if report['returncode']:
                    raise ValueError('AI runtime dependency installation failed: ' + report['output'][-8000:])

    def environment(self, project_id, python=None, working_directory=None):
        project = self.project(project_id)
        cwd = self._working_directory(project, working_directory)
        return inspect_environment(dependency_root(cwd, project['path']), python)

    def project_outputs(self, project_id):
        project = self.project(project_id)
        detection = project.get('detection') or {}
        path_value = detection.get('detected_output_path')
        if not path_value:
            return {'path': None, 'confidence': detection.get('confidence', 'uncertain'), 'exists': False, 'executions': []}
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = Path(project['path']) / path
        previous = [
            {'execution_id': r['execution_id'], 'status': r.get('status'), 'command': r.get('command', []), 'seed': r.get('seed'), 'repeat': r.get('repeat')}
            for r in self.records() if r.get('project') == project['path'] and r.get('detected_output_path') == path_value
        ]
        return {'path': path_value, 'absolute_path': str(path.resolve()), 'confidence': detection.get('confidence', 'uncertain'), 'exists': path.is_dir(), 'executions': previous}

    def _working_directory(self, project, directory=None):
        root = Path(project['path'])
        path = Path(directory or root).expanduser()
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_dir() or (path != root and root not in path.parents):
            raise ValueError('Working directory must be inside the registered project')
        return path

    def prepare_project_environment(self, project_id, python=None, working_directory=None):
        project = self.project(project_id)
        cwd = self._working_directory(project, working_directory)
        if not working_directory and not any((cwd / name).is_file() for name in MANIFESTS):
            defaults = self.defaults(project_id)
            cwd = self._working_directory(project, defaults['working_directory'])
        root = dependency_root(cwd, project['path'])
        interpreter = python_path(python)
        with self.lock:
            if project_id in self.environment_jobs:
                return project.get('runtime', {})
            event = threading.Event()
            self.environment_jobs[project_id] = event
            project['runtime'] = {'status': 'queued', 'python': interpreter, 'dependency_root': str(root)}
            self._write(self.registry, self.projects)
            self.environment_pool.submit(self._prepare_project, project, root, interpreter, event)
            return project['runtime']

    def _prepare_project(self, project, root, interpreter, event):
        def update(report):
            with self.lock:
                project['runtime'] = {**report, 'python': interpreter, 'dependency_root': str(root)}
                self._write(self.registry, self.projects)
        try:
            if event.is_set():
                update({'status': 'cancelled'})
                return
            report = prepare_environment(root, interpreter, cancel=event, update=update)
            with self.lock:
                project['runtime'] = {**report, 'python': interpreter, 'dependency_root': str(root)}
                self._write(self.registry, self.projects)
        except Exception as exc:
            update({'status': 'failed', 'error': str(exc)})
        finally:
            with self.lock:
                self.environment_jobs.pop(project['id'], None)

    def llm_config(self):
        config = load_config(self.root)
        return config.public_dict() if config else {"provider": "openai-compatible", "base_url": "", "model": "", "api_key_configured": False, "timeout_seconds": 45}

    def configure_llm(self, data):
        with self.lock:
            config = self._llm_draft(data)
            return save_config(self.root, config.private_dict()).public_dict()

    def _llm_draft(self, data):
        existing = load_config(self.root)
        payload = dict(data)
        if payload.get("clear_api_key"):
            payload["api_key"] = ""
        elif not payload.get("api_key") and existing:
            draft = validate_config(payload)
            if draft.base_url == existing.base_url:
                payload["api_key"] = existing.api_key
        return validate_config(payload)

    def test_llm(self, data=None):
        config = load_config(self.root)
        if data is not None:
            config = self._llm_draft(data)
        if config is None:
            raise ValueError("Configure an LLM provider first")
        return test_connection(config)

    def launch(self, data):
        project = self.project(data["project_id"])
        plan = self.command_plan(data) if 'selections' in data else None
        argv = shlex.split(data["command"]) if isinstance(data["command"], str) else data["command"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("Enter a command")
        seeds = [None] if plan else data.get("seeds", [None])
        repeats = 1 if plan else data.get("repeats", 1)
        if not isinstance(seeds, list) or not seeds or not all(s is None or type(s) is int and 0 <= s < 2**32 for s in seeds):
            raise ValueError("Seeds must be unsigned 32-bit integers")
        if type(repeats) is not int or not 1 <= repeats <= 20 or len(seeds) * repeats > 64:
            raise ValueError("At most 64 executions and 20 repeats per request")
        if any(s is not None for s in seeds) and not any("{seed}" in arg or "{seeds}" in arg for arg in argv):
            raise ValueError("A seeded command must contain {seed}")
        if repeats > 1 and not any("{repeat}" in arg for arg in argv):
            raise ValueError("Repeated commands must contain {repeat}")
        if not plan and seeds == [None] and any("{seed}" in arg or "{seeds}" in arg for arg in argv):
            raise ValueError("Enter at least one seed")
        overrides = data.get("output") or None
        if overrides is not None and (not isinstance(overrides, str) or "\x00" in overrides):
            raise ValueError("Output override must be a path")
        env = data.get("environment") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and "=" not in k and "\x00" not in k + v for k, v in env.items()):
            raise ValueError("Environment must be a JSON object of strings")
        working_directory = data.get("working_directory") or project["path"]
        working_directory = Path(working_directory).expanduser().resolve()
        project_root = Path(project["path"])
        if not working_directory.is_dir() or (working_directory != project_root and project_root not in working_directory.parents):
            raise ValueError("Working directory must be an existing directory inside the project")
        python = python_path(data.get('python'))
        auto_install = data.get('auto_install', True)
        if type(auto_install) is not bool:
            raise ValueError('auto_install must be a boolean')
        is_python = bool(re.fullmatch(r'python(?:\d+(?:\.\d+)?)?(?:\.exe)?', Path(argv[0]).name))
        if is_python:
            if not data.get('python') and Path(argv[0]).is_absolute():
                python = python_path(argv[0])
            argv[0] = python
        pending = []
        contract = next((e for e in self.catalog(project['id'])['entries'] if e['id'] == data.get('entry_id')), {})
        commit = _git_commit(Path(project['path']))
        if plan:
            entries = plan['executions']
        else:
            entries = []
            for repeat in range(1, repeats + 1):
                for seed in seeds:
                    def substitute(value):
                        return value.replace('{seed}', str(seed)).replace('{seeds}', str(seed)).replace('{repeat}', str(repeat))
                    entries.append({'command': [substitute(a) for a in argv], 'seed': seed, 'repeat': repeat,
                                    'output': substitute(overrides) if overrides else None})
        for entry in entries:
            command = list(entry['command'])
            if is_python:
                command[0] = python
            metadata = self._plan_metadata(project, {**entry, 'command': command}, data, contract, commit)
            metadata.supervisor_id = self.instance_id
            metadata.algorithm = entry.get('algorithm')
            metadata.rl_environment = entry.get('rl_environment')
            metadata.training_steps = entry.get('steps')
            metadata.environment_setup = {'status': 'queued', 'python': python}
            metadata.launch_environment = dict(env)
            pending.append(metadata)
        start_immediately = data.get('start_immediately', True)
        if type(start_immediately) is not bool:
            raise ValueError('start_immediately must be a boolean')
        with self.lock:
            for metadata in pending:
                metadata.environment_setup.update(start_immediately=start_immediately, auto_install=auto_install, prepare_environment=is_python)
                metadata.write(self.root)
                event = threading.Event()
                if start_immediately:
                    self.jobs[metadata.execution_id] = event
                    self.pool.submit(self._execute, metadata, env, event, python if is_python else None, auto_install)
                else:
                    self.held.add(metadata.execution_id)
                    self.held_events[metadata.execution_id] = event
                    metadata.status = 'held'
                    metadata.write(self.root)
        return [m.execution_id for m in pending]

    def _execute(self, metadata, env, event, python=None, auto_install=True):
        try:
            acquired = False
            claimed = False
            known = decision(metadata, self.records(), probe=False)
            if not event.is_set() and known['action'] == 'skipped_completed':
                apply_decision(metadata, known)
                metadata.end_time = _now(); metadata.write(self.root)
                return
            if metadata.training_contract:
                import hashlib
                for source, digest in metadata.training_contract.get('source_hashes', {}).items():
                    if hashlib.sha256((Path(metadata.project) / source).read_bytes()).hexdigest() != digest:
                        apply_decision(metadata, {'action': 'needs_attention', 'completed_steps': 0, 'target_steps': metadata.training_steps,
                            'remaining_steps': metadata.training_steps, 'checkpoint': None, 'message': 'Project source changed since AI analysis; analyze again'})
                        metadata.end_time = _now(); metadata.write(self.root)
                        return
            while not event.is_set():
                with self.lock:
                    snapshot = self.resources()
                    allocation = self._allocation(metadata, snapshot)
                    identity_available = not metadata.configuration_key or metadata.configuration_key not in self.training_claims
                    if identity_available and allocation is not None and self.reserved_slots < self.max_parallel and snapshot.get('cpu_percent', 0) < self.policy['cpu_limit']:
                        self.reserved_slots += 1
                        self.allocations[metadata.execution_id] = allocation
                        if metadata.configuration_key:
                            self.training_claims.add(metadata.configuration_key)
                            claimed = True
                        if allocation.get('gpu_id') is not None:
                            env = {**env, 'CUDA_VISIBLE_DEVICES': str(allocation['gpu_id'])}
                        acquired = True
                        break
                metadata.status = 'queued'; metadata.environment_setup = {**metadata.environment_setup, 'waiting_for_resources': True}; metadata.write(self.root)
                time.sleep(1)
            if python and not event.is_set():
                metadata.status = 'preparing'
                def update(report):
                    metadata.environment_setup.update(report)
                    metadata.write(self.root)
                runtime = metadata.training_contract.get('runtime', {})
                root = Path(metadata.project) / runtime['dependency_directory'] if runtime else dependency_root(metadata.working_directory, metadata.project)
                report = prepare_environment(root, python, auto_install=auto_install, cancel=event, update=update, env=env)
                if report['status'] != 'ready':
                    metadata.status = 'cancelled' if event.is_set() else 'failed'
                    metadata.error = report.get('error', 'Environment preparation failed')
                    metadata.exit_code, metadata.end_time = (130 if event.is_set() else 126), _now()
                    metadata.write(self.root)
                    return
                info = report['inspection_after']
                if runtime:
                    self._validate_runtime(runtime, info, python, root, env, auto_install, event)
                metadata.environment.update(python=info['python_version'], executable=python, prefix=info['prefix'])
                env = {**env, 'PATH': str(Path(python).parent) + os.pathsep + env.get('PATH', os.environ.get('PATH', ''))}
                launcher = info.get('mpi_launcher') or {}
                if launcher.get('path'):
                    env['PATH'] = str(Path(launcher['path']).parent) + os.pathsep + env['PATH']
                    metadata.environment['mpi_launcher'] = launcher['path']
                # Module entry points may re-exec their script path in MPI children.
                env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', os.environ.get('PYTHONPATH', ''))
                if info['conda_prefix']:
                    env.update(CONDA_PREFIX=info['conda_prefix'], CONDA_DEFAULT_ENV=info['conda_env'])
            if not event.is_set() and metadata.configuration_key:
                result = decision(metadata, self.records())
                apply_decision(metadata, result)
                metadata.write(self.root)
                if result['action'] in {'skipped_completed', 'needs_attention'}:
                    metadata.end_time = _now()
                    metadata.write(self.root)
                    return
            def track(process):
                with self.lock:
                    self.processes[metadata.execution_id] = process
                    self.live_metadata[metadata.execution_id] = metadata
            execute_execution(metadata, self.root, env=env, cancel=event, on_process=track)
            recovery = metadata.training_contract.get('recovery')
            completion = metadata.training_contract.get('completion') or {}
            if metadata.task == 'train':
                if metadata.status == 'success' and (completion.get('success_means_target') or (recovery or {}).get('success_means_target')):
                    metadata.completed_steps = metadata.training_steps
                    metadata.remaining_steps = 0
                elif recovery:
                    observed = decision(metadata, [], probe=True)
                    if observed['action'] in {'resume', 'skipped_completed'}:
                        metadata.completed_steps = observed['completed_steps']
                        metadata.checkpoint = observed['checkpoint']
                metadata.write(self.root)
        except Exception as exc:
            metadata.status, metadata.error = 'failed', str(exc)
            metadata.exit_code, metadata.end_time = 126, _now()
            metadata.write(self.root)
        finally:
            with self.lock:
                if 'acquired' in locals() and acquired:
                    self.reserved_slots = max(0, self.reserved_slots - 1)
                self.allocations.pop(metadata.execution_id, None)
                if claimed:
                    self.training_claims.discard(metadata.configuration_key)
                self.jobs.pop(metadata.execution_id, None)
                self.processes.pop(metadata.execution_id, None)
                self.live_metadata.pop(metadata.execution_id, None)
                self.held.discard(metadata.execution_id)

    def start_execution(self, execution_id):
        with self.lock:
            record = self.record(execution_id)
            if execution_id in self.jobs:
                return {'status': record['status'], 'execution_id': execution_id}
            if record.get('status') != 'held':
                raise ValueError('Only held executions can be started')
            record['supervisor_id'] = self.instance_id
            event = self.jobs.get(execution_id) or self.held_events.pop(execution_id, None)
            if event is None:
                event = threading.Event(); self.jobs[execution_id] = event
            else:
                self.jobs[execution_id] = event
            self.held.discard(execution_id)
            record['status'] = 'queued'; self._write(self.root / 'executions' / f'{execution_id}.json', record)
            self.pool.submit(self._execute_record, record, event)
        return {'status': 'queued', 'execution_id': execution_id}

    def _execute_record(self, record, event):
        fields = ExecutionMetadata.__dataclass_fields__
        payload = {k: v for k, v in record.items() if k in fields}
        payload.setdefault('resource_request', {})
        payload.setdefault('launch_environment', {})
        payload.setdefault('stdout', '')
        payload.setdefault('stderr', '')
        metadata = ExecutionMetadata(**payload)
        env = metadata.launch_environment
        python = metadata.environment_setup.get('python') if metadata.environment_setup.get('prepare_environment', True) else None
        auto_install = metadata.environment_setup.get('auto_install', True)
        self._execute(metadata, env, event, python, bool(auto_install))

    def pause(self, execution_id):
        with self.lock:
            process = self.processes.get(execution_id)
            if not process or process.poll() is not None:
                raise ValueError('Execution is not currently running')
            if os.name != 'posix':
                raise ValueError('Pause is only supported on POSIX systems')
            os.killpg(process.pid, signal.SIGSTOP)
            metadata = self.live_metadata[execution_id]
            metadata.status = 'paused'
            metadata.write(self.root)
        return {'status': 'paused'}

    def resume(self, execution_id):
        with self.lock:
            process = self.processes.get(execution_id)
            if not process or process.poll() is not None:
                raise ValueError('Execution process is no longer available')
            if self.live_metadata[execution_id].status != 'paused':
                raise ValueError('Execution is not paused')
            if os.name != 'posix':
                raise ValueError('Resume is only supported on POSIX systems')
            os.killpg(process.pid, signal.SIGCONT)
            metadata = self.live_metadata[execution_id]
            metadata.status = 'running'
            metadata.write(self.root)
        return {'status': 'running'}

    def cancel(self, execution_id):
        with self.lock:
            if execution_id in self.held_events:
                event = self.held_events.pop(execution_id)
                event.set()
                self.held.discard(execution_id)
                record = self.record(execution_id)
                record.update(status='cancelled', exit_code=130, end_time=_now())
                self._write(self.root / 'executions' / f'{execution_id}.json', record)
                return
            if execution_id not in self.jobs:
                raise ValueError("Execution is not managed by this server or has already finished")
            process = self.processes.get(execution_id)
            record = self.record(execution_id)
            if record.get('status') == 'paused' and process and os.name == 'posix':
                os.killpg(process.pid, signal.SIGCONT)
            self.jobs[execution_id].set()

    def open_output(self, execution_id):
        record = next((r for r in self.records() if r["execution_id"] == execution_id), None)
        if not record or not record.get("detected_output_path"):
            raise ValueError("Execution has no confirmed output location")
        path = Path(record["detected_output_path"]).expanduser()
        if not path.is_absolute():
            path = Path(record.get("working_directory") or record["project"]) / path
        # Only open the location with the OS. Never enumerate or serve its files.
        if not path.is_dir():
            raise ValueError("Output directory does not exist yet")
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            completed = subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)], capture_output=True, timeout=10)
            if completed.returncode:
                raise ValueError("Desktop file manager unavailable; copy the output path instead")
        return str(path)

    def close(self):
        with self.lock:
            for event in self.jobs.values():
                event.set()
            for event in self.held_events.values():
                event.set()
            for event in self.environment_jobs.values():
                event.set()
        self.pool.shutdown(wait=True)
        self.environment_pool.shutdown(wait=True)
        self.lockfile.close()
