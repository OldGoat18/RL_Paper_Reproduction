"""Run a project's command while leaving its output handling untouched."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .detector import DetectionResult, detect_output_paths
from .metadata import ExecutionMetadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(project: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def run_project(
    project_path: str | Path,
    command: List[str],
    *,
    task: str = "train",
    seed: Optional[int] = None,
    repeat: Optional[int] = None,
    metadata_root: str | Path = ".harness",
    output_override: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[ExecutionMetadata, DetectionResult, int]:
    project = Path(project_path).expanduser().resolve()
    detection = detect_output_paths(project)
    execution_id = uuid.uuid4().hex
    metadata = ExecutionMetadata(
        execution_id=execution_id,
        project=str(project),
        git_commit=_git_commit(project),
        task=task,
        environment={key: os.environ[key] for key in ("PYTHON_VERSION", "CUDA_VISIBLE_DEVICES", "CONDA_DEFAULT_ENV") if key in os.environ},
        command=command,
        seed=seed,
        repeat=repeat,
        start_time=_now(),
        end_time=None,
        exit_code=None,
        status="running",
        detected_output_path=output_override if output_override is not None else detection.detected_output_path,
        output_path_confidence="override" if output_override is not None else detection.confidence,
    )
    metadata.write(metadata_root)
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    try:
        completed = subprocess.run(command, cwd=project, env=process_env, check=False)
        exit_code = completed.returncode
    except OSError:
        exit_code = 127
    metadata.end_time = _now()
    metadata.exit_code = exit_code
    metadata.status = "success" if exit_code == 0 else "failed"
    metadata.write(metadata_root)
    return metadata, detection, exit_code
