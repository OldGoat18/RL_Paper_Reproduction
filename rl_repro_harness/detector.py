"""Conservative discovery from project sources, never training artifacts."""
from __future__ import annotations

import ast
import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


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
    method: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_KEY = re.compile(r"^(?:outputs?|results?|checkpoints?|logs?|save|run|experiment|tensorboard|wandb)(?:[_-]?(?:path|dir|directory))?$", re.I)
_NAMES = {"output", "outputs", "result", "results", "checkpoint", "checkpoints", "log", "logs", "save", "run", "runs", "experiment", "experiments"}
_DOC = re.compile(r"\b(?:outputs?|results?|checkpoints?|logs?|save|run|experiment)[ _-]*(?:path|dir|directory)?\s*[:=]\s*[`\"']?([^\s`\"',)]+)", re.I)
_FLAGS = {"--output", "--output-dir", "--output-path", "--results-dir", "--result-path", "--checkpoint-dir", "--log-dir", "--save-dir", "--run-dir"}
_DIRS = {"config", "configs", "configuration", "conf", "docs", "doc", "scripts", "src"}
_IGNORE = {"tests", "node_modules", "venv", "__pycache__", "runs", "results", "outputs", "checkpoints", "logs", "wandb", "artifacts", "build", "dist"}
_CONFIG = {".json", ".toml", ".yaml", ".yml"}


def _add(items: list[OutputCandidate], value: str, source: str, confidence: str, evidence: str) -> None:
    value = value.strip().strip("`\"'")
    if not value or any(c in value for c in ("\n", "\r", "\x00")) or value.startswith(("http:", "https:")):
        return
    if any(c in value for c in ("$", "{", "}", "<", ">")):
        confidence = "uncertain"
    value = value if value == "/" else value.rstrip("/\\") or "."
    if not any(c.path == value and c.source == source for c in items):
        items.append(OutputCandidate(value, source, confidence, evidence))


def _walk(value: Any, keys: tuple[str, ...], source: str, items: list[OutputCandidate]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            if isinstance(child, str) and (_KEY.fullmatch(name) or (name in {"path", "dir", "directory"} and set(keys) & _NAMES)):
                _add(items, child, source, "high", ".".join((*keys, name)))
            _walk(child, (*keys, name), source, items)
    elif isinstance(value, list):
        for child in value:
            _walk(child, keys, source, items)


def collect_sources(root: Path) -> dict[str, str]:
    """Only visit source trees; unknown directories are not presumed sources."""
    sources: dict[str, str] = {}
    def visit(directory: Path, in_sources: bool = False, depth: int = 0) -> None:
        if len(sources) >= 100 or depth > 8:
            return
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or path.name.startswith(".") or path.name in _IGNORE:
                continue
            if path.is_dir():
                project_marker = any((path / marker).is_file() for marker in ("README", "README.md", "README.rst", "pyproject.toml", "setup.py", "requirements.txt"))
                if in_sources or path.name in _DIRS or (path / "__init__.py").is_file() or project_marker:
                    visit(path, True, depth + 1)
                continue
            suffix = path.suffix.lower()
            is_config = suffix in _CONFIG and (in_sources or any(word in path.stem.lower() for word in ("config", "setting", "param")))
            if suffix not in {".md", ".rst", ".py", ".sh", ".bash"} and not is_config:
                continue
            if len(sources) >= 100:
                return
            try:
                if path.stat().st_size <= 128_000:
                    sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
    visit(root)
    return sources


def _command_candidates(command: Sequence[str], items: list[OutputCandidate], source: str = "command") -> None:
    for index, arg in enumerate(command):
        key, sep, value = arg.partition("=")
        if key in _FLAGS:
            if not sep and index + 1 < len(command):
                value = command[index + 1]
            if value and not value.startswith("--"):
                _add(items, value, source, "high" if source == "command" else "medium", key)


def detect_output_paths(project_path: str | Path, command: Sequence[str] = ()) -> DetectionResult:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project path is not a directory: {root}")
    candidates: list[OutputCandidate] = []
    _command_candidates(command, candidates)
    for source, text in collect_sources(root).items():
        suffix = Path(source).suffix.lower()
        try:
            if suffix in _CONFIG:
                parsed = json.loads(text) if suffix == ".json" else tomllib.loads(text) if suffix == ".toml" else yaml.safe_load(text)
                _walk(parsed, (), source, candidates)
            elif suffix == ".py":
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and _KEY.fullmatch(target.id):
                                _add(candidates, node.value.value, source, "medium", target.id)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                        if set(flags) & _FLAGS:
                            for kw in node.keywords:
                                if kw.arg == "default" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                    _add(candidates, kw.value.value, source, "medium", str(flags))
            elif suffix in {".md", ".rst"}:
                for match in _DOC.finditer(text):
                    _add(candidates, match.group(1), source, "medium", match.group(0))
            else:
                for line in text.splitlines():
                    _command_candidates(shlex.split(line, comments=True), candidates, source)
        except (ValueError, SyntaxError, yaml.YAMLError):
            continue
    eligible = [c for c in candidates if c.source == "command"] or candidates
    paths = {c.path for c in eligible}
    selected = next(iter(paths)) if len(paths) == 1 and all(c.confidence != "uncertain" for c in eligible) else None
    confidence = ("high" if any(c.confidence == "high" for c in eligible) else "medium") if selected else "uncertain"
    return DetectionResult(str(root), tuple(candidates), selected, confidence)
