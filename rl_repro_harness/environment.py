"""Prepare project-declared dependencies in the selected Python environment."""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from packaging.specifiers import SpecifierSet
from .dependencies import parse_dependency

try:
    import tomllib
except ImportError:
    import tomli as tomllib


PREPARATION_LOCK = threading.Lock()
MANIFESTS = ('requirements.txt', 'requirements .txt', 'pyproject.toml', 'setup.cfg', 'setup.py', 'environment.yml', 'environment.yaml')


def current_python():
    prefix = os.environ.get('CONDA_PREFIX')
    candidate = Path(prefix) / ('python.exe' if os.name == 'nt' else 'bin/python') if prefix else None
    return str(candidate) if candidate and candidate.is_file() else sys.executable


def python_path(value=None):
    value = str(value or current_python())
    located = shutil.which(value) if not Path(value).is_absolute() else value
    path = Path(located or value).expanduser().absolute()
    if not path.is_file():
        raise ValueError('Selected Python interpreter does not exist')
    # Resolving a venv's symlink would silently switch back to its base Python.
    return str(path)


def conda_executable():
    return os.environ.get('CONDA_EXE') or shutil.which('conda') or next((str(p) for p in [Path(sys.base_prefix) / 'bin/conda', Path(sys.base_prefix) / 'Scripts/conda.exe'] if p.is_file()), None)


def conda_environments():
    interpreter = python_path()
    result = [{'name': os.environ.get('CONDA_DEFAULT_ENV', 'current'), 'python': interpreter, 'current': True}]
    conda = conda_executable()
    if conda:
        try:
            response = subprocess.run([conda, 'env', 'list', '--json'], capture_output=True, text=True, timeout=10)
            for prefix in json.loads(response.stdout).get('envs', []):
                path = Path(prefix) / ('python.exe' if os.name == 'nt' else 'bin/python')
                if path.is_file() and str(path) != interpreter:
                    result.append({'name': Path(prefix).name, 'python': str(path), 'current': False})
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return result


def dependency_root(directory, boundary=None):
    directory = Path(directory).resolve()
    boundary = Path(boundary or directory).resolve()
    for candidate in (directory, *directory.parents):
        if candidate != boundary and boundary not in candidate.parents:
            break
        if any((candidate / name).is_file() for name in MANIFESTS):
            return candidate
    return directory


def dependency_plan(project):
    root = Path(project).resolve()
    plan = {'dependency_files': [], 'requirements': [], 'constraints': [], 'pip_options': [], 'conda': [], 'channels': [], 'python_requires': None, 'compatibility': []}

    def add_dependency(line, source, constraint=False):
        try:
            if constraint:
                Requirement(line)
            else:
                parse_dependency(line, root)
        except ValueError as exc:
            raise ValueError(f"{source}: {exc}") from exc
        plan['constraints' if constraint else 'requirements'].append(line)

    def pip_option(line):
        words = shlex.split(line)
        key, sep, value = words[0].partition('=')
        with_value = {'--index-url', '-i', '--extra-index-url', '--find-links', '-f',
                      '--trusted-host', '--only-binary', '--no-binary'}
        if key in with_value:
            if (sep and len(words) != 1) or (not sep and len(words) != 2):
                raise ValueError("Expected one value for " + key)
            value = value if sep else words[1]
            if not value or value.startswith('-'):
                raise ValueError("Invalid value for " + key)
            plan['pip_options'].extend([key, value])
            return True
        if key in {'--no-index', '--pre', '--prefer-binary'} and len(words) == 1 and not sep:
            plan['pip_options'].append(key)
            return True
        return False

    def requirements_file(path, constraint=False, visited=None):
        visited = set() if visited is None else visited
        path = path.resolve()
        if path == root or root not in path.parents or path in visited or len(visited) > 32:
            raise ValueError('Invalid or recursive requirements include')
        visited.add(path)
        plan['dependency_files'].append(str(path.relative_to(root)))
        content = re.sub(r"\\\r?\n", "", path.read_text(encoding='utf-8'))
        for number, line in enumerate(content.splitlines(), 1):
            line = re.split(r'\s+#', line, 1)[0].strip()
            if not line or line.startswith('#'):
                continue
            include = re.match(r'^(?:-r\s*|--requirement(?:=|\s+))(.+)$', line)
            constraints = re.match(r'^(?:-c\s*|--constraint(?:=|\s+))(.+)$', line)
            if include or constraints:
                filename = shlex.split((include or constraints).group(1))
                if len(filename) != 1:
                    raise ValueError(f"{path}:{number}: Invalid requirements include")
                requirements_file(path.parent / filename[0], constraint or bool(constraints), visited)
            else:
                try:
                    handled = pip_option(line) if line.startswith('-') else False
                except ValueError as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc
                if not handled:
                    add_dependency(line, f"{path}:{number}", constraint)
        visited.remove(path)

    req = root / ('requirements.txt' if (root / 'requirements.txt').is_file() else 'requirements .txt')
    if req.is_file():
        requirements_file(req)
    pyproject = root / 'pyproject.toml'
    if pyproject.is_file():
        plan['dependency_files'].append('pyproject.toml')
        document = tomllib.loads(pyproject.read_text(encoding='utf-8'))
        metadata = document.get('project', {})
        for item in metadata.get('dependencies', []):
            add_dependency(item, str(pyproject))
        plan['python_requires'] = metadata.get('requires-python')
        if 'dependencies' in metadata.get('dynamic', []):
            raise ValueError('Dynamic pyproject dependencies require a static requirements declaration')
    setup = root / 'setup.py'
    if setup.is_file():
        plan['dependency_files'].append('setup.py')
        tree = ast.parse(setup.read_text(encoding='utf-8'))
        literals = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            literals[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (getattr(node.func, 'id', '') == 'setup' or getattr(node.func, 'attr', '') == 'setup'):
                for kw in node.keywords:
                    if kw.arg in {'install_requires', 'python_requires'}:
                        try:
                            value = literals[kw.value.id] if isinstance(kw.value, ast.Name) else ast.literal_eval(kw.value)
                        except (ValueError, TypeError, KeyError):
                            raise ValueError('Cannot statically resolve setup.py dependencies; provide requirements.txt')
                        if kw.arg == 'install_requires':
                            for item in value:
                                add_dependency(item, str(setup))
                        else:
                            plan['python_requires'] = value
    cfg = root / 'setup.cfg'
    if cfg.is_file():
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(cfg)
        plan['dependency_files'].append('setup.cfg')
        for line in parser.get('options', 'install_requires', fallback='').splitlines():
            if line.strip():
                add_dependency(line.strip(), str(cfg))
        plan['python_requires'] = parser.get('options', 'python_requires', fallback=plan['python_requires'])
    envfile = next((root / name for name in ('environment.yml', 'environment.yaml') if (root / name).is_file()), None)
    if envfile:
        plan['dependency_files'].append(envfile.name)
        document = yaml.safe_load(envfile.read_text(encoding='utf-8')) or {}
        plan['channels'] = document.get('channels', [])
        for dep in document.get('dependencies', []):
            if isinstance(dep, str):
                plan['conda'].append(dep)
            elif isinstance(dep, dict) and set(dep) == {'pip'}:
                for line in dep['pip']:
                    if not pip_option(line):
                        add_dependency(line, str(envfile) + ":pip")
            else:
                raise ValueError('Unsupported Conda dependency declaration')
    for item in plan['requirements']:
        parse_dependency(item, root)
    for item in plan['constraints']:
        Requirement(item)
    return plan


def dependency_specs(project):
    plan = dependency_plan(project)
    return plan['dependency_files'], plan['requirements']


def _run(command, cwd, cancel=None, timeout=600, env=None, output_limit=12000):
    from .runner import _terminate
    import time
    process = subprocess.Popen(command, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', start_new_session=os.name == 'posix')
    deadline = time.monotonic() + timeout
    while True:
        try:
            output, _ = process.communicate(timeout=0.2)
            return {'command': command, 'returncode': process.returncode, 'output': output[-output_limit:]}
        except subprocess.TimeoutExpired:
            if (cancel and cancel.is_set()) or time.monotonic() >= deadline:
                _terminate(process)
                output, _ = process.communicate()
                return {'command': command, 'returncode': 130 if cancel and cancel.is_set() else 124, 'output': output[-12000:]}


_IDENTITY = '''import sys,json,platform,os,importlib.metadata as m
print(json.dumps(dict(python=sys.executable,prefix=sys.prefix,python_version=platform.python_version(),
platform_system=platform.system(),platform_machine=platform.machine(),os_name=os.name,sys_platform=sys.platform,
implementation_name=sys.implementation.name,implementation_version=platform.python_version(),
platform_python_implementation=platform.python_implementation(),
packages={d.metadata['Name']:d.version for d in m.distributions() if d.metadata['Name']})))'''


def mpi_launcher(interpreter, library, root, env=None):
    """Match the launcher to the MPI library loaded by the selected Python."""
    def family(text):
        text = text.lower()
        if 'open mpi' in text or 'openrte' in text:
            return 'openmpi'
        if 'mpich' in text or 'hydra' in text:
            return 'mpich'
        return None
    expected = family(library)
    search_path = os.pathsep.join([str(Path(interpreter).parent), (env or {}).get('PATH', os.environ.get('PATH', '')), os.defpath])
    checked = set()
    for directory in search_path.split(os.pathsep):
        candidate = Path(directory) / 'mpirun'
        if not candidate.is_file() or str(candidate) in checked:
            continue
        checked.add(str(candidate))
        result = _run([str(candidate), '--version'], root, timeout=5, env=env)
        if expected and not result['returncode'] and family(result['output']) == expected:
            return {'path': str(candidate), 'family': expected}
    return {'error': 'No mpirun matching the selected Python MPI library (' + (expected or 'unknown') + ') was found'}


def inspect_environment(project, python=None, env=None):
    interpreter = python_path(python)
    root = Path(project).resolve()
    plan = dependency_plan(root)
    result = _run([interpreter, '-c', _IDENTITY], root, timeout=20, env=env, output_limit=2_000_000)
    if result['returncode']:
        raise ValueError('Cannot inspect selected Python: ' + result['output'])
    identity = json.loads(result['output'])
    packages = {canonicalize_name(k): v for k, v in identity.pop('packages').items()}
    marker_env = {**identity, 'python_full_version': identity['python_version'], 'python_version': '.'.join(identity['python_version'].split('.')[:2]), 'extra': ''}
    missing = []
    for text in plan['requirements']:
        dependency = parse_dependency(text, root)
        req = dependency.requirement
        if req is None:
            missing.append(text)
            continue
        if req.marker and not req.marker.evaluate(marker_env):
            continue
        version = packages.get(canonicalize_name(req.name))
        if version is None or not req.specifier.contains(version, prereleases=True) or req.extras or dependency.source:
            missing.append(text)
    constraints = [Requirement(text) for text in plan['constraints']]
    for req in constraints:
        if req.marker and not req.marker.evaluate(marker_env):
            continue
        for text in plan['requirements']:
            base = parse_dependency(text, root).requirement
            if base is None:
                continue
            version = packages.get(canonicalize_name(base.name))
            if canonicalize_name(base.name) == canonicalize_name(req.name) and version and not req.specifier.contains(version, prereleases=True):
                missing.append(str(req))
    errors = []
    if plan['python_requires'] and not SpecifierSet(plan['python_requires']).contains(identity['python_version']):
        errors.append(f"Project requires Python {plan['python_requires']}; selected {identity['python_version']}")
    conda_prefix = Path(identity['prefix'])
    is_conda = (conda_prefix / 'conda-meta').is_dir()
    native = None
    launcher = None
    named = [parse_dependency(r, root).requirement for r in plan['requirements']]
    if any(req and canonicalize_name(req.name) == 'mpi4py' for req in named) and 'mpi4py' in packages:
        native = _run([interpreter, '-c', 'from mpi4py import MPI; print(MPI.Get_library_version())'], root, timeout=20, env=env)
        if not native['returncode']:
            launcher = mpi_launcher(interpreter, native['output'], root, env)
    conda_pending = []
    installed_conda = {}
    if is_conda:
        for path in (conda_prefix / 'conda-meta').glob('*.json'):
            value = json.loads(path.read_text())
            installed_conda[value['name']] = value
    for spec in plan['conda']:
        name, *version_spec = re.split(r'(?=[=<>!~])', spec.split('::')[-1], maxsplit=1)
        version = version_spec[0] if version_spec else ''
        if name == 'python':
            expected = version.lstrip('=')
            matches = identity['python_version'].startswith(expected.rstrip('*')) if version.startswith('=') and not version.startswith('==') else not version or SpecifierSet(version).contains(identity['python_version'])
            if not matches:
                errors.append('Conda file requires ' + spec + '; select a matching Python environment')
        else:
            installed = installed_conda.get(name)
            satisfied = bool(installed)
            if installed and version:
                if version.startswith('=') and not version.startswith('=='):
                    parts = version.lstrip('=').split('=')
                    satisfied = installed['version'].startswith(parts[0].rstrip('*')) and (len(parts) == 1 or installed.get('build') == parts[1])
                else:
                    satisfied = SpecifierSet(version).contains(installed['version'])
            if not satisfied:
                conda_pending.append(spec)
    if conda_pending and not is_conda:
        errors.append('Project declares Conda packages; select a Conda Python environment')
    launcher_error = launcher and launcher.get('error')
    if launcher_error:
        errors.append(launcher_error)
    return {**identity, **plan, 'python': interpreter, 'conda_env': ('base' if conda_prefix.name.lower() in {'anaconda3', 'miniconda3'} else conda_prefix.name) if is_conda else None, 'conda_prefix': str(conda_prefix) if is_conda else None, 'dependency_root': str(root), 'declared_dependencies': plan['requirements'], 'missing_dependencies': sorted(set(missing)), 'conda_pending': conda_pending, 'native_check': native, 'mpi_launcher': launcher, 'errors': errors, 'ready': not (missing or errors or conda_pending or native and native['returncode'])}


def install_missing(project, python, missing, cancel=None, env=None, pip_options=()):
    if not missing:
        return {'attempted': False, 'installed': [], 'returncode': 0, 'output': ''}
    arguments = [arg for value in missing for arg in parse_dependency(value, project).install_args]
    command = [python_path(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--upgrade-strategy', 'only-if-needed', *pip_options, *arguments]
    result = _run(command, project, cancel, env=env)
    return {**result, 'attempted': True, 'installed': missing if result['returncode'] == 0 else []}


def prepare_environment(project, python=None, *, auto_install=True, cancel=None, update=None, env=None):
    notify = update or (lambda state: None)
    with PREPARATION_LOCK:
        report = {'status': 'checking', 'steps': [], 'python': python_path(python)}
        notify(report.copy())
        info = inspect_environment(project, python, env=env)
        report['inspection'] = info
        if info['errors']:
            report.update(status='failed', error='; '.join(info['errors']))
        elif not info['ready'] and not auto_install:
            report.update(status='failed', error='Dependencies are not ready and automatic installation is disabled')
        else:
            if info['conda_pending']:
                conda = conda_executable()
                if not conda:
                    raise ValueError('Conda executable not found')
                command = [conda, 'install', '--yes', '--prefix', info['prefix'], '--freeze-installed']
                for channel in info['channels']:
                    command += ['--channel', channel]
                command += info['conda_pending']
                report.update(status='installing', command=command)
                notify(report.copy())
                report['steps'].append(_run(command, project, cancel, env=env))
            if not any(s['returncode'] for s in report['steps']) and info['missing_dependencies']:
                report.update(status='installing', packages=info['missing_dependencies'])
                notify(report.copy())
                names = {canonicalize_name(req.name) for m in info['missing_dependencies']
                         for req in [parse_dependency(m, project).requirement] if req is not None}
                constraints = [r for r in info['constraints'] if canonicalize_name(Requirement(r).name) in names]
                report['steps'].append(install_missing(project, info['python'], info['missing_dependencies'] + constraints, cancel, env, pip_options=info['pip_options']))
            bad = next((s for s in report['steps'] if s['returncode']), None)
            if bad:
                report.update(status='cancelled' if bad['returncode'] == 130 else 'failed', error=bad['output'] or 'Dependency installer failed')
            else:
                checked = inspect_environment(project, info['python'], env=env) if report['steps'] else info
                report['inspection_after'] = checked
                # pip itself resolves extras; a successful install verifies those requirements.
                unresolved = [r for r in checked['missing_dependencies']
                              for dep in [parse_dependency(r, project)]
                              if not dep.source and not dep.requirement.extras]
                # Source targets need a successful pip resolution in this preparation.
                if not report['steps']:
                    unresolved.extend(r for r in checked['missing_dependencies'] if parse_dependency(r, project).source)
                ready = not (unresolved or checked['errors'] or checked['conda_pending'] or checked['native_check'] and checked['native_check']['returncode'])
                report.update(status='ready' if ready else 'failed')
                if not ready:
                    report['error'] = checked['native_check']['output'] if checked['native_check'] and checked['native_check']['returncode'] else 'Dependency verification failed: ' + ', '.join(unresolved + checked['errors'])
        notify(report.copy())
        return report
