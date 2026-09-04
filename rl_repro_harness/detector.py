"""Detect output locations from an RL project's own source and documentation.

Detection is deliberately conservative. The harness reports candidates and
confidence; it never creates, moves, or inspects files in a candidate output
directory.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # Python 3.9/3.10 without optional dependencies
    tomllib = None  # type: ignore


@dataclass(frozen=True)
class OutputCandidate:
    path: str
    source: str
    confidence: str
    evidence: str


@dataclass(frozen=True)
class DetectionResult:
    project_path: str
    candidates: tuple[OutputCandidate, ...]
    detected_output_path: Optional[str]
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "detected_output_path": self.detected_output_path,
            "confidence": self.confidence,
        }


_KEY_RE = re.compile(
    r"(?:output|outputs|result|results|checkpoint|checkpoints|log|logs|" r"save|run|experiment|tensorboard|wandb)(?:_|-)?(?:path|dir|directory)?$",
    re.IGNORECASE,
)
_CLI_RE = re.compile(
    r"(?:--(?:output|output-dir|results-dir|result-path|checkpoint-dir|log-dir|save-dir|run-dir)|"
    r"(?:output|results|checkpoint|log|save|run)[_-](?:path|dir))\s*(?:=|\s)\s*([\"']?[^\s,\"']+)",
    re.IGNORECASE,
)
_DOC_RE = re.compile(
    r"(?:output|result|checkpoint|log|save|run|experiment)[ _-]*(?:path|dir|directory)?\s*[:=]\s*`?([.~/][^`\s,)]+)",
    re.IGNORECASE,
)
_YAML_NESTED_RE = re.compile(
    r"^\s*(?:outputs?|results?|checkpoints?|logs?|runs?|experiments?)\s*:\s*$\n"
    r"\s+(?:path|dir|directory)\s*:\s*([\"']?[^#\s]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _is_path_like(value: str) -> bool:
    value = value.strip().strip("'\"")
    if not value or value.startswith("http://") or value.startswith("https://"):
        return False
    return (
        value.startswith((".", "~/", "/"))
        or "/" in value
        or "\\" in value
        or value.lower().endswith(("runs", "results", "logs", "checkpoints"))
    )


def _add(candidates: list[OutputCandidate], path: str, source: str, confidence: str, evidence: str) -> None:
    path = path.strip().strip("'\"").rstrip("/\\") or "."
    if _is_path_like(path) and not any(c.path == path for c in candidates):
        candidates.append(OutputCandidate(path, source, confidence, evidence))


def _walk_config(value: Any, key_path: tuple[str, ...], source: str, candidates: list[OutputCandidate]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_name = str(key)
            parent_names = {part.lower() for part in key_path}
            is_output_namespace = bool(parent_names & {"output", "outputs", "result", "results", "checkpoint", "checkpoints", "log", "logs", "save", "run", "experiment"})
            if isinstance(child, str) and (_KEY_RE.search(key_name) or (key_name.lower() in {"path", "dir", "directory"} and is_output_namespace)):
                _add(candidates, child, source, "high", ".".join((*key_path, key_name)))
            _walk_config(child, (*key_path, key_name), source, candidates)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_config(child, (*key_path, str(index)), source, candidates)


def _parse_structured(path: Path) -> Optional[Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        if path.suffix.lower() == ".toml" and tomllib is not None:
            return tomllib.loads(text)
        if path.suffix.lower() in {".yaml", ".yml"}:
            # Avoid making the harness require PyYAML. This handles the common
            # scalar path form while leaving richer YAML to text scanning.
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:\s*([\"']?[^#]+?)\s*$", line)
                if match:
                    parsed[match.group(1)] = match.group(2).strip().strip("'\"")
            return parsed
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return None


def detect_output_paths(project_path: str | Path) -> DetectionResult:
    """Inspect project metadata and return the most defensible output path.

    A missing or ambiguous path is represented as ``None`` with ``uncertain``
    confidence, so callers can ask the user to override it.
    """

    root = Path(project_path).expanduser().resolve()
    candidates: list[OutputCandidate] = []
    if not root.is_dir():
        raise NotADirectoryError(f"Project path is not a directory: {root}")

    files: Iterable[Path] = (
        path for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".harness" not in path.parts
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml"}:
            parsed = _parse_structured(path)
            if parsed is not None:
                _walk_config(parsed, (), relative, candidates)
        if path.suffix.lower() in {".md", ".rst", ".txt", ".py", ".sh", ".bash", ".yaml", ".yml", ".toml", ".json"}:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in _CLI_RE.finditer(text):
                _add(candidates, match.group(1), relative, "medium", match.group(0))
            if path.suffix.lower() in {".yaml", ".yml"}:
                for match in _YAML_NESTED_RE.finditer(text):
                    _add(candidates, match.group(1), relative, "high", match.group(0).replace("\n", " "))
            if path.suffix.lower() in {".md", ".rst", ".txt"}:
                for match in _DOC_RE.finditer(text):
                    _add(candidates, match.group(1), relative, "medium", match.group(0))

    high = [candidate for candidate in candidates if candidate.confidence == "high"]
    if len(high) == 1:
        selected, confidence = high[0].path, "high"
    elif len(candidates) == 1:
        selected, confidence = candidates[0].path, candidates[0].confidence
    else:
        selected, confidence = None, "uncertain"
    return DetectionResult(str(root), tuple(candidates), selected, confidence)
