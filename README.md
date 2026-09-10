# RL Reproduction Harness

This repository contains a small execution harness for reinforcement-learning paper reproductions. The original project owns all training outputs: model weights, checkpoints, logs, metrics, plots, videos, and evaluation artifacts remain in the locations chosen by that project. The harness only detects and reports those locations and records minimal execution metadata.

## Install

```bash
python -m pip install -e . --no-build-isolation
```

The harness uses PyYAML and, on Python 3.9/3.10, tomli for configuration parsing.

The Web workbench optionally connects to an OpenAI-compatible chat completions
endpoint for source analysis. Open the local workbench, choose **Project setup**,
then **Configure AI**. The API key is stored in the metadata directory's
`llm.json` in plaintext with mode `0600`; it is never returned to the browser.
Blank keys retain the saved key only for the same endpoint; **Clear saved key**
removes it. **Test connection** tests the unsaved form without saving it or sending
project sources. A configured connection is selected by default for analysis. The endpoint
receives only bounded README/documentation/configuration/source text and a
strict JSON request. The model has no tools and cannot execute commands or
write files. Harness validation accepts a returned path only when its exact
evidence and source file are present in the submitted context; otherwise the
candidate is discarded or marked uncertain. Static analysis remains available
without an LLM. Tool calls, incomplete responses and extra response fields are
rejected. The harness stores only validated analysis metadata in `projects.json`;
it never saves raw model responses or generates files inside the RL project.
Exact quotations verify provenance, not semantic truth: review candidate evidence
and use the output override when a path remains uncertain.

For an API-compatible service, configure a base URL such as
`https://api.example.com/v1`; the harness appends `/chat/completions` unless
the URL already ends with that path. The same integration can be configured
non-interactively with `RL_HARNESS_LLM_URL`, `RL_HARNESS_LLM_MODEL`, and the
optional `RL_HARNESS_LLM_API_KEY` environment variables.

## Detect a project's output path

The Web **Project setup** view also lists source-derived algorithms, environment
IDs, runnable scripts, README commands and CLI flags for multi-project
repositories. Register `~/AI/RL/Safe-Reinforcement-Learning-Baselines`, select
an entry's **Configure** action, and review its command, working directory,
environment ID, seeds and environment variables before launching. Existing
project commands are preserved; missing or incompatible dependencies are not
silently installed or replaced. Execution status and metadata are visualized
in the Experiments view, while project-specific training artifacts remain
owned and interpreted by the original project.

Before a run starts, Harness inspects the selected Python interpreter and the
nearest project dependency manifests (`requirements.txt`, `setup.py`,
`pyproject.toml`, `setup.cfg`, and Conda environment files). The Web form
defaults to the current Conda/Python environment and exposes other local Conda
interpreters. Missing declared packages are installed into the selected
interpreter only when **Install declared missing dependencies** is enabled;
native imports such as `from mpi4py import MPI` are verified after installation.
The run remains in `preparing` until this check succeeds. Compatibility signals
visible in source are checked too: for example, legacy
`gym.envs.registry.all()` code causes the project’s selected environment to be
checked against an older Gym range instead of silently running with Gym 0.26+.
Installation output and the final environment inspection are retained in
execution metadata. A failed preparation never launches the training command.

```bash
rl-harness detect /path/to/rl-project
```

Detection reads README/documentation, JSON/TOML/YAML configuration, and common training scripts. The result contains evidence and confidence. If there is no single defensible path, `detected_output_path` is `null` and confidence is `uncertain`; provide an explicit override when running.

## Run a project

```bash
rl-harness run /path/to/rl-project --task train --seed 1 -- python train.py --config configs/cartpole.yaml
```

The command runs with the project's working directory and output behavior unchanged. A metadata record is written to `.harness/executions/<execution_id>.json` (or `--metadata-root`). The record includes the command, timestamps, exit status, Git commit, seed/repeat, environment hints, and detected or overridden output path. The harness never copies, renames, reorganizes, or interprets files in that output path.

## Project layout

```text
rl_repro_harness/       # detector, metadata model, runner, CLI
tests/                   # focused behavior tests
```

Run tests with `python -m unittest discover -s tests`.

## PPO CartPole baseline and web viewer

The self-contained baseline in `examples/ppo_cartpole` uses Stable-Baselines3 PPO. Install its optional dependencies, then run:

```bash
python examples/ppo_cartpole/train.py --seed 1 --timesteps 10000
rl-harness web --metadata-root .harness
```

The baseline writes its model and metrics to its own `runs/` directory. The web viewer only reads execution metadata and displays the detected output location; it does not copy or interpret project artifacts.
