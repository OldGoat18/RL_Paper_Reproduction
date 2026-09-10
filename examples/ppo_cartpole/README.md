# PPO / CartPole-v1

Project-owned baseline using Stable-Baselines3 PPO (Schulman et al., 2017,
https://arxiv.org/abs/1707.06347). This verifies a reproducible training workflow;
it does not reproduce the paper's Atari or MuJoCo benchmark tables.

Install `requirements.txt` in your training environment. CPU execution uses one
Torch thread and deterministic algorithms. Dependencies, seed, hyperparameters,
requested and actual rollout-rounded timesteps are recorded in each run.

```bash
python train.py --seed 1 --repeat 1 --output ./runs/seed_1/repeat_1
python evaluate.py --model ./runs/seed_1/repeat_1/model.zip --seed 1 --episodes 20 --output ./runs/seed_1/repeat_1/evaluation
python report.py --runs ./runs --output ./reports
```

The project owns `runs/` and `reports/`. It writes `model.zip`, checkpoints,
configuration snapshots, training CSV logs, a training plot, and independent
evaluation JSON. Evaluation uses a separate environment with seed + 10000 and
a deterministic policy. Return statistics are not a claim of solving CartPole
or matching the PPO paper. Compare independent seeds using this project's report.

Existing nonempty training directories are rejected. `--resume` continues an
existing final model with the same seed and hyperparameters; it is not a
bit-exact replay of interrupted environment state. Use a new output location
for an independent run. Repeats retain the same seed deliberately.

`harness.project.json` supplies project-authored command presets. The harness
substitutes `{seed}` and `{repeat}` in those explicit commands, without defining
or modifying this project's artifact structure. `{python}` selects the server's
Python; edit the command to use another training environment.
