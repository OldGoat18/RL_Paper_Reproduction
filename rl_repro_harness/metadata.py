"""Execution metadata persisted outside the training project's output tree."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, metadata_root: str | Path) -> Path:
        destination = Path(metadata_root).expanduser() / "executions" / f"{self.execution_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return destination
