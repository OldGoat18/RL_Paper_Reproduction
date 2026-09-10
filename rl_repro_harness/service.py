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
from .llm import detect_with_llm, load_config, save_config, test_connection, validate_config
from .runner import _now, execute_execution, prepare_execution
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
        self.pool = ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="rl-execution")
        self.environment_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rl-environment")
        self.jobs = {}
        self.environment_jobs = {}
        self.processes = {}
        self.live_metadata = {}
        self.held = set()
        self.held_events = {}
        self.reserved_slots = 0
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
                if "runtime" not in existing:
                    self.prepare_project_environment(existing['id'])
                return existing
            project = {"id": uuid.uuid4().hex, "name": (name or path.name)[:120], "path": str(path), "created_at": _now(), "detection": None}
            manifest = path / "harness.project.json"
            if manifest.is_file():
                spec = json.loads(manifest.read_text(encoding="utf-8"))
                project["presets"] = spec.get("tasks", {})
                project["name"] = name or spec.get("name", path.name)
            self.projects.append(project)
            self._write(self.registry, self.projects)
            self.prepare_project_environment(project['id'])
            return project

    def project(self, project_id):
        with self.lock:
            result = next((p for p in self.projects if p["id"] == project_id), None)
        if not result:
            raise ValueError("Project not found")
        return result

    def catalog(self, project_id):
        project = self.project(project_id)
        root = Path(project["path"])
        ignored = {".git", ".venv", "venv", "__pycache__", "node_modules", "runs", "results", "logs", "data", "artifacts", "build", "dist"}
        algorithms = {"ppo", "sac", "td3", "ddpg", "dqn", "a2c", "trpo", "pcpo", "cpo", "lagrangian", "mbpo", "mbrl", "safety layer", "leave no trace"}
        environments = set(); entries = []; commands = []
        command_re = re.compile(r"(?m)^\s*((?:python(?:\d+(?:\.\d+)?)?|python\s+-m)\s+[^\n`]+)")
        for doc in sorted(list(root.rglob("README")) + list(root.rglob("README.md")) + list(root.rglob("README.rst"))):
            if any(part in ignored for part in doc.parts):
                continue
            try:
                text = doc.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in command_re.finditer(text):
                command = match.group(1).strip().rstrip(".;")
                if "python" not in command.lower() or any(x in command for x in ("|", ">", "&&", "<")) or len(command) > 500:
                    continue
                command_algorithms = sorted(name for name in algorithms if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", command.lower()))
                command_envs = re.findall(r"(?:[A-Za-z][A-Za-z0-9_]*-(?:v|V)\d+|Safety[A-Za-z0-9_-]+)", command)
                directory = str(doc.parent.relative_to(root) or Path("."))
                command_entry = {"id": f"command:{doc.relative_to(root)}:{len(commands)}", "path": command, "directory": directory, "source": str(doc.relative_to(root)), "command": command, "algorithms": command_algorithms or ["Unclassified"], "environments": command_envs, "flags": re.findall(r"--[A-Za-z0-9_-]+", command), "confidence": "high" if command_algorithms and command_envs else "medium", "runnable": True}
                commands.append(command_entry)
                entries.append(command_entry)
        for path in sorted(root.rglob("*.py")):
            if any(part in ignored for part in path.parts) or len(entries) >= 300:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            environments.update(re.findall(r"(?:[A-Za-z][A-Za-z0-9_]*-(?:v|V)\d+|Safety[A-Za-z0-9_-]+)", text))
            lower = text.lower() + " " + path.stem.lower()
            found = sorted(name for name in algorithms if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", lower))
            runnable = bool(re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", text)) or path.name.startswith(("train", "run", "main", "experiment", "benchmark"))
            if not runnable:
                continue
            parser_flags = []; parser_defaults = {}
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                        values = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                        if values:
                            parser_flags.extend(values)
                            default = next((kw.value.value for kw in node.keywords if kw.arg == "default" and isinstance(kw.value, ast.Constant)), None)
                            if default is not None:
                                for value in values:
                                    parser_defaults[value] = str(default)
            except SyntaxError:
                pass
            entries.append({"id": str(path.relative_to(root)), "path": str(path.relative_to(root)), "directory": str(path.parent.relative_to(root) or Path('.')), "source": str(path.relative_to(root)), "command": f"{{python}} {path.relative_to(root)}", "algorithms": found or ["Unclassified"], "environments": sorted(re.findall(r"(?:[A-Za-z][A-Za-z0-9_]*-(?:v|V)\d+|Safety[A-Za-z0-9_-]+)", text))[:32], "flags": sorted(set(parser_flags))[:64], "defaults": parser_defaults, "confidence": "medium" if found else "uncertain", "runnable": True})
        # Keep the catalog useful without overwhelming the UI with duplicate README/script entries.
        unique = []
        seen = set()
        for entry in entries:
            key = entry["command"]
            if key not in seen:
                seen.add(key); unique.append(entry)
        return {"project_id": project_id, "project_path": str(root), "algorithms": sorted({a for e in unique for a in e["algorithms"]}), "environments": sorted(environments), "entries": unique[:300], "notes": ["Entries are source-derived; the harness does not install dependencies or modify project files.", "Old, missing, or incompatible dependencies remain the responsibility of each subproject.", "Commands shown from README files are project-authored examples; verify dependencies and arguments before starting a run."]}

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
        detection = detect_output_paths(project["path"], shlex.split(command.replace("{python}", sys.executable)) if command else ())
        return {"project_id": project_id, "task": "train", "command": command.replace("{python}", sys.executable) if command else "", "working_directory": directory, "environment": environment or "", "seeds": seed_default or ("1" if command and "{seed}" in command else ""), "repeats": "1", "output": detection.detected_output_path or "", "source": "project preset" if preset else (entry.get("source") if entry else None), "confidence": "high" if command else "uncertain"}

    def analyze(self, project_id, command="", llm=False):
        project = self.project(project_id)
        argv = shlex.split(command) if isinstance(command, str) else command
        if llm:
            config = load_config(self.root)
            if config is None:
                raise ValueError("Configure an LLM provider before LLM analysis")
            result = detect_with_llm(project["path"], argv, config=config).to_dict()
        else:
            result = detect_output_paths(project["path"], argv).to_dict()
        with self.lock:
            project["detection"] = result
            self._write(self.registry, self.projects)
        return result

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
        return {"projects": self.projects, "executions": self.records(), "llm_available": config is not None, "llm": config.public_dict() if config else None, "llm_error": config_error, "python": current_python(), "python_environments": self.python_environments, "metadata_root": str(self.root), "resources": self.resources()}

    def resources(self):
        try:
            import psutil
            memory = psutil.virtual_memory()
            return {'cpu_percent': psutil.cpu_percent(interval=None), 'cpu_count': psutil.cpu_count() or 1,
                    'memory_percent': memory.percent, 'memory_available_bytes': memory.available,
                    'memory_total_bytes': memory.total, 'parallel_limit': self.max_parallel,
                    'running': self.reserved_slots, 'active': len(self.jobs) + len(self.held)}
        except ImportError:
            load = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
            return {'cpu_percent': round(load * 100 / max(os.cpu_count() or 1, 1), 1), 'cpu_count': os.cpu_count() or 1,
                    'memory_percent': None, 'memory_available_bytes': None, 'memory_total_bytes': None,
                    'parallel_limit': self.max_parallel, 'running': self.reserved_slots, 'active': len(self.jobs) + len(self.held)}

    def environment(self, project_id, python=None, working_directory=None):
        project = self.project(project_id)
        cwd = self._working_directory(project, working_directory)
        return inspect_environment(dependency_root(cwd, project['path']), python)

    def project_outputs(self, project_id):
        project = self.project(project_id)
        detection = project.get('detection') or detect_output_paths(project['path']).to_dict()
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
        plan = build_commands(data) if 'selections' in data else None
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
            metadata, _ = prepare_execution(project['path'], command, task=data.get('task', 'train'),
                                            seed=entry['seed'], repeat=entry['repeat'], output_override=entry['output'],
                                            env=env, working_directory=working_directory)
            metadata.supervisor_id = self.instance_id
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
            while not event.is_set():
                with self.lock:
                    snapshot = self.resources()
                    if self.reserved_slots < self.max_parallel and snapshot.get('cpu_percent', 0) < 90 and (snapshot.get('memory_percent') is None or snapshot['memory_percent'] < 92):
                        self.reserved_slots += 1
                        acquired = True
                        break
                metadata.status = 'queued'; metadata.environment_setup = {**metadata.environment_setup, 'waiting_for_resources': True}; metadata.write(self.root)
                time.sleep(1)
            if python and not event.is_set():
                metadata.status = 'preparing'
                def update(report):
                    metadata.environment_setup.update(report)
                    metadata.write(self.root)
                root = dependency_root(metadata.working_directory, metadata.project)
                report = prepare_environment(root, python, auto_install=auto_install, cancel=event, update=update, env=env)
                if report['status'] != 'ready':
                    metadata.status = 'cancelled' if event.is_set() else 'failed'
                    metadata.error = report.get('error', 'Environment preparation failed')
                    metadata.exit_code, metadata.end_time = (130 if event.is_set() else 126), _now()
                    metadata.write(self.root)
                    return
                info = report['inspection_after']
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
            def track(process):
                with self.lock:
                    self.processes[metadata.execution_id] = process
                    self.live_metadata[metadata.execution_id] = metadata
            execute_execution(metadata, self.root, env=env, cancel=event, on_process=track)
        except Exception as exc:
            metadata.status, metadata.error = 'failed', str(exc)
            metadata.exit_code, metadata.end_time = 126, _now()
            metadata.write(self.root)
        finally:
            with self.lock:
                if 'acquired' in locals() and acquired:
                    self.reserved_slots = max(0, self.reserved_slots - 1)
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
