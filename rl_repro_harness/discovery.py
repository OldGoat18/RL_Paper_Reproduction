"""Bounded discovery of project-owned entry points and argument contracts."""
import ast
import re
import shlex
from pathlib import Path
import yaml

from .detector import collect_sources
from .environment import MANIFESTS, dependency_plan
from .commands import ALIASES, STEP_FLAGS

CONTAINERS = {"external", "projects", "baselines", "third_party", "third-party", "repos"}
OUTPUT_DIRS = {"data", "results", "runs", "logs", "outputs", "checkpoints", "artifacts",
               "wandb", "experiments", "training_logs", "videos", "figures",
               "node_modules", "venv", "__pycache__", "build", "dist"}
LIMITS = {"max_subprojects": 64, "files_per_subproject": 100,
          "max_directories": 2048, "max_depth": 8,
          "source_bytes_per_file": 128000, "max_executions": 4096,
          "project_writes": False, "output_contents_read": False}


def project_roots(root):
    roots = [root]
    visited = 0
    def visit(parent, depth=0):
        nonlocal visited
        if depth > LIMITS["max_depth"] or visited >= LIMITS["max_directories"]:
            return
        visited += 1
        try:
            children = sorted(parent.iterdir())
        except OSError:
            return
        for child in children:
            if len(roots) >= LIMITS["max_subprojects"]:
                return
            if (not child.is_dir() or child.is_symlink() or child.name.startswith(".")
                    or child.name in OUTPUT_DIRS or child.name.startswith(("data_", "results_", "runs_", "logs_"))):
                continue
            marker = any((child / name).is_file() for name in (*MANIFESTS, "README.md", "README", "requirements .txt"))
            if marker:
                roots.append(child)
                visit(child, depth + 1)
            else:
                visit(child, depth + 1)
    visit(root)
    return roots


def argument_contract(text):
    flags, defaults, choices = [], {}, {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return flags, defaults, choices
    tyro_classes = {node.args[0].id for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "tyro"
                   and node.func.attr == "cli" and node.args and isinstance(node.args[0], ast.Name)}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name not in tyro_classes:
            continue
        for field in cls.body:
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                flag = "--" + field.target.id.replace("_", "-")
                flags.append(flag)
                if isinstance(field.value, ast.Constant):
                    defaults[flag] = str(field.value.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = getattr(node.func, "attr", "")
        args = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
        keys = []
        default = None
        if method == "add_argument":
            keys = [a for a in args if isinstance(a, str) and a.startswith("--")]
        elif method == "add_arg" or method.startswith("DEFINE_"):
            if args and isinstance(args[0], str):
                keys = ["--" + args[0].lstrip("-")]
                default = args[1] if len(args) > 1 else None
        if not keys:
            continue
        flags.extend(keys)
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                default = kw.value.value
            if kw.arg == "choices":
                try:
                    values = ast.literal_eval(kw.value)
                    if isinstance(values, (list, tuple)):
                        for key in keys:
                            choices[key] = [str(v) for v in values]
                except (ValueError, TypeError):
                    pass
            if kw.arg == "help" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                match = re.search(r"Choose from:\s*\{([^}]+)\}", kw.value.value)
                if match:
                    for key in keys:
                        choices[key] = [v.strip() for v in match.group(1).split(",")]
        if default is not None:
            for key in keys:
                defaults[key] = str(default)
    return sorted(set(flags)), defaults, choices


def discover(root, include_dependencies=False):
    root = Path(root).resolve()
    entries, subprojects = [], []
    for subroot in project_roots(root):
        rel = subroot.relative_to(root).as_posix()
        source_error = None
        try:
            sources = collect_sources(subroot)
        except OSError as exc:
            sources = {}
            source_error = str(exc)
        # Each subproject has its own scan budget; descendants belong to their own entry.
        sources = {name: text for name, text in sources.items()
                   if not any(part in CONTAINERS for part in Path(name).parts)}
        sub = {"id": rel, "name": subroot.name, "path": str(subroot), "limits": LIMITS,
               "files_scanned": len(sources), "warnings": []}
        if len(sources) >= 100:
            sub["warnings"].append("Source scan limit reached")
        if source_error:
            sub["warnings"].append(source_error)
        if include_dependencies:
            try:
                sub["dependencies"] = dependency_plan(subroot)
                sub["dependency_status"] = "parsed" if sub["dependencies"]["dependency_files"] else "undeclared"
            except (ValueError, OSError, SyntaxError, yaml.YAMLError) as exc:
                sub.update(dependency_status="unsupported", dependency_error=str(exc))
        subprojects.append(sub)
        examples = []
        for name, text in sources.items():
            if not name.lower().startswith("readme"):
                continue
            text = re.sub(r"\\\n\s*", " ", text)
            for match in re.finditer(r"(?m)^\s*(python[\d.]*\s+[^\n\x60]+)", text):
                try:
                    argv = shlex.split(match.group(1).strip())
                except ValueError:
                    continue
                if len(argv) > 1 and not any(x in match.group(1) for x in ("$", ">", "|", "&&")):
                    examples.append((name, argv))
        for name, text in sources.items():
            if not name.endswith(".py"):
                continue
            if not (re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", text) or Path(name).stem.startswith(("train", "main", "run", "experiment"))):
                continue
            flags, defaults, choices = argument_contract(text)
            matching = [argv for _, argv in examples if len(argv) > 1 and
                        (argv[1] == name or (len(argv) > 2 and argv[1] == "-m" and argv[2].replace(".", "/") + ".py" == name))]
            example = next(iter(matching), None)
            module = Path(name).with_suffix("").as_posix().replace("/", ".")
            fallback = ["{python}", "-m", module] if (subroot / Path(name).parent / "__init__.py").is_file() else ["{python}", name]
            argv = list(example or fallback)
            for i, arg in enumerate(argv):
                key, sep, value = arg.partition("=")
                if key.startswith("--"):
                    flags.append(key)
                    if sep or i + 1 < len(argv):
                        defaults[key] = value if sep else argv[i + 1]
            bindings = {k: next((f for f in aliases if f in flags), None) for k, aliases in ALIASES.items()}
            alg_flag, env_flag = bindings["algorithms"], bindings["environments"]
            algs = choices.get(alg_flag, []) or ([defaults[alg_flag]] if alg_flag in defaults else [])
            if not algs:
                algs = [subroot.name if Path(name).stem in {"main", "train", "run"} else Path(name).stem]
            envs = choices.get(env_flag, []) or ([defaults[env_flag]] if env_flag in defaults else [])
            envs += re.findall(r"\b(?:[A-Za-z][A-Za-z0-9_]*/)?[A-Za-z][A-Za-z0-9_]*-v\d+\b", text)
            from .commands import describe_command
            for documented in matching:
                values = describe_command(documented)
                if alg_flag:
                    algs += values["algorithms"]
                if env_flag:
                    envs += values["environments"]
            domain = defaults.get("--domain")
            task = defaults.get("--task")
            env_args = {}
            if domain and task:
                envs = [domain + "/" + task]
                for script_name, script in sources.items():
                    if script_name.endswith(".sh"):
                        match = re.search(r"(?m)^\s*tasks=\(([^)]+)\)", script)
                        if match:
                            envs += [domain + "/" + v for v in shlex.split(match.group(1))]
                env_args = {v: ["--domain", v.split("/", 1)[0], "--task", v.split("/", 1)[1]] for v in set(envs)}
            step = next((f for f in STEP_FLAGS if f in flags), None)
            entry = {"id": (Path(rel) / name).as_posix(), "path": (Path(rel) / name).as_posix(),
                     "source": (Path(rel) / name).as_posix(), "directory": rel,
                     "subproject": rel, "project_name": subroot.name,
                     "command": shlex.join(argv), "flags": sorted(set(flags)), "defaults": defaults,
                     "algorithms": sorted(set(algs)), "environments": sorted(set(envs)),
                     "bindings": bindings, "environment_arguments": env_args,
                     "steps_flag": step, "confidence": "high" if example or flags else "uncertain",
                     "runnable": True,
                     "warnings": [] if step else ["Training step argument is unconfirmed; configure the command explicitly"]}
            entries.append(entry)
    return {"project_path": str(root), "entries": entries, "subprojects": subprojects,
            "algorithms": sorted({a for e in entries for a in e["algorithms"]}),
            "environments": sorted({a for e in entries for a in e["environments"]}),
            "limits": LIMITS, "notes": ["Static source analysis; unsupported declarations remain explicit.",
                                       "Only execution metadata establishes completion; output contents are not parsed."]}
