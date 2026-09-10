"""Execute argv without a shell, leaving stdout and artifacts with the project."""
from __future__ import annotations

import os
import platform
import signal
import subprocess
import threading
import uuid
import codecs
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .detector import DetectionResult, detect_output_paths
from .metadata import ExecutionMetadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(project: Path) -> Optional[str]:
    try:
        result = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def prepare_execution(project_path, command, *, task="train", seed=None, repeat=None, output_override=None, env=None, working_directory=None):
    project = Path(project_path).expanduser().resolve()
    cwd = Path(working_directory).expanduser().resolve() if working_directory else project
    if not cwd.is_dir() or (cwd != project and project not in cwd.parents):
        raise ValueError("Working directory must be inside the registered project")
    if not command or not all(isinstance(arg, str) and "\x00" not in arg for arg in command):
        raise ValueError("Command must be a nonempty argument list")
    detection = detect_output_paths(project, command)
    effective_env = {**os.environ, **(env or {})}
    hints = {key: effective_env[key] for key in ("CUDA_VISIBLE_DEVICES", "CONDA_DEFAULT_ENV", "VIRTUAL_ENV") if key in effective_env}
    hints.update(python=platform.python_version(), platform=platform.platform())
    metadata = ExecutionMetadata(
        execution_id=uuid.uuid4().hex, project=str(project), git_commit=_git_commit(project),
        task=task, environment=hints, command=list(command), seed=seed, repeat=repeat,
        start_time=_now(), end_time=None, exit_code=None, status="queued",
        detected_output_path=output_override if output_override is not None else detection.detected_output_path,
        output_path_confidence="override" if output_override is not None else detection.confidence,
        output_candidates=detection.to_dict()["candidates"], working_directory=str(cwd),
    )
    return metadata, detection


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGCONT)
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
    except ProcessLookupError:
        process.wait()


def execute_execution(metadata, metadata_root, *, env=None, cancel=None, on_process=None) -> int:
    cancel = cancel or threading.Event()
    process = None
    code = 127
    metadata.start_time = _now()
    metadata.status = "running"
    metadata.write(metadata_root)
    try:
        if cancel.is_set():
            metadata.status, code = "cancelled", 130
        else:
            effective_env = {**os.environ, **(env or {})}
            # Remove launcher markers inherited from an outer MPI/CI process;
            # the project controls its own MPI fork and must start cleanly.
            for key in ("PMI_SIZE", "PMI_RANK", "PMI_FD", "PMIX_RANK", "OMPI_COMM_WORLD_SIZE", "OMPI_COMM_WORLD_RANK"):
                effective_env.pop(key, None)
            process = subprocess.Popen(metadata.command, cwd=metadata.working_directory or metadata.project, env=effective_env, start_new_session=os.name == "posix", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if on_process:
                on_process(process)
            captured = {"stdout": "", "stderr": ""}
            capture_lock = threading.Lock()
            def collect(stream_name, stream):
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                try:
                    while True:
                        chunk = os.read(stream.fileno(), 4096)
                        text = decoder.decode(chunk, final=not chunk)
                        with capture_lock:
                            captured[stream_name] = (captured[stream_name] + text)[-32000:]
                        if not chunk:
                            break
                finally:
                    stream.close()
            readers = [threading.Thread(target=collect, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
            for reader in readers: reader.start()
            last_snapshot = time.monotonic()
            while True:
                try:
                    code = process.wait(timeout=0.2)
                    metadata.status = "success" if code == 0 else "failed"
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - last_snapshot >= 1:
                        with capture_lock:
                            metadata.stdout, metadata.stderr = captured['stdout'], captured['stderr']
                        metadata.write(metadata_root)
                        last_snapshot = time.monotonic()
                    if cancel.is_set():
                        _terminate(process)
                        metadata.status, code = "cancelled", 130
                        break
            for reader in readers: reader.join(timeout=2)
            metadata.stdout, metadata.stderr = captured['stdout'], captured['stderr']
            if code != 0 and not metadata.error:
                metadata.error = (metadata.stderr or metadata.stdout or f"Process exited with status {code}")[-12000:]
    except KeyboardInterrupt:
        if process:
            _terminate(process)
        metadata.status, code = "cancelled", 130
    except (OSError, ValueError) as exc:
        metadata.status, metadata.error = "failed", str(exc)
    finally:
        metadata.end_time, metadata.exit_code = _now(), code
        metadata.write(metadata_root)
    return code


def run_project(project_path, command, *, task="train", seed=None, repeat=None, metadata_root=".harness", output_override=None, env=None, cancel=None, working_directory=None):
    metadata, detection = prepare_execution(project_path, command, task=task, seed=seed, repeat=repeat, output_override=output_override, env=env, working_directory=working_directory)
    code = execute_execution(metadata, Path(metadata_root).expanduser().resolve(), env=env, cancel=cancel)
    return metadata, detection, code
