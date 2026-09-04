"""A small harness for running RL projects without owning their outputs."""

from .detector import DetectionResult, OutputCandidate, detect_output_paths
from .metadata import ExecutionMetadata

__all__ = [
    "DetectionResult",
    "ExecutionMetadata",
    "OutputCandidate",
    "detect_output_paths",
]
