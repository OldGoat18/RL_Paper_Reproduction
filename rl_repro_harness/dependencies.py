"""Classify pip install targets separately from PEP 508 requirements."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from packaging.requirements import InvalidRequirement, Requirement


@dataclass
class Dependency:
    text: str
    requirement: Optional[Requirement]
    install_args: list[str]
    source: bool = False


def parse_dependency(value, root):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Dependency must be a nonempty string")
    text = value.strip()
    editable = re.match(r"^(?:-e\s*|--editable(?:=|\s+))(.+)$", text)
    target = editable.group(1).strip() if editable else text
    if len(target) >= 2 and target[0] in "'\"" and target[-1] == target[0]:
        target = target[1:-1]
    if not target or target.startswith("-"):
        raise ValueError("Unsupported pip directive: " + text)
    args = ["--editable", target] if editable else [target]
    local = Path(target).expanduser()
    candidate = local if local.is_absolute() else Path(root) / local
    looks_local = target.startswith((".", "/", "~")) or target.endswith((".whl", ".tar.gz", ".zip"))
    if "://" not in target and (looks_local or candidate.exists()):
        resolved = str(candidate.resolve())
        return Dependency(text, None, ["--editable", resolved] if editable else [resolved], True)
    try:
        req = Requirement(target)
    except InvalidRequirement:
        req = None
    if req is not None and (not editable or req.url):
        return Dependency(text, req, args, bool(req.url))
    location = urlsplit(target)
    if location.scheme in {"http", "https", "file", "git", "git+https", "git+http",
                           "git+ssh", "git+file", "hg+https", "hg+ssh",
                           "svn+https", "svn+ssh", "bzr+ssh"}:
        egg = parse_qs(location.fragment).get("egg", [None])[0]
        return Dependency(text, Requirement(egg) if egg else None, args, True)
    raise ValueError("Invalid dependency or unsupported install target: " + text)
