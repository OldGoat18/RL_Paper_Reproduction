# RL Reproduction Harness

This repository contains a small execution harness for reinforcement-learning paper reproductions. The original project owns all training outputs: model weights, checkpoints, logs, metrics, plots, videos, and evaluation artifacts remain in the locations chosen by that project. The harness only detects and reports those locations and records minimal execution metadata.

## Install

```bash
python -m pip install -e .
```

The package uses only the Python standard library.

## Detect a project's output path

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
