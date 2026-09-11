"""Execution metadata persisted outside the training project's output tree."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ExecutionMetadata:
    execution_id: str
    project: str
    git_commit: Optional[str]
    task: str
    environment: dict[str, str]
    command: list[str]
    seed: Optional[int]
    repeat: Optional[int]
    start_time: str
    end_time: Optional[str]
    exit_code: Optional[int]
    status: str
    detected_output_path: Optional[str]
    output_path_confidence: str
    output_candidates: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    supervisor_id: Optional[str] = None
    working_directory: Optional[str] = None
    environment_setup: dict[str, object] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    resource_request: dict[str, object] = field(default_factory=dict)
    launch_environment: dict[str, str] = field(default_factory=dict)
    algorithm: Optional[str] = None
    rl_environment: Optional[str] = None
    training_steps: Optional[int] = None
    configuration_key: Optional[str] = None
    training_contract: dict[str, Any] = field(default_factory=dict)
    training_action: Optional[str] = None
    training_decision: dict[str, Any] = field(default_factory=dict)
    completed_steps: Optional[int] = None
    remaining_steps: Optional[int] = None
    checkpoint: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, metadata_root: str | Path) -> Path:
        destination = Path(metadata_root).expanduser() / "executions" / f"{self.execution_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.to_dict(), stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
