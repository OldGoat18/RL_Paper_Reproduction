"""Project-owned PPO training, checkpoints, evaluation and figures."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import gymnasium
import stable_baselines3
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.logger import configure

from evaluate import evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timesteps", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    timesteps = args.timesteps if args.timesteps is not None else config["total_timesteps"]
    if timesteps <= 0 or not 0 <= args.seed < 2**32 or args.repeat < 1:
        parser.error("Timesteps/repeat must be positive and seed must be an unsigned 32-bit integer")
    out = args.output or Path(__file__).parent / "runs" / f"seed_{args.seed}" / f"repeat_{args.repeat}"
    if args.resume:
        if not (out / "model.zip").is_file():
            parser.error("Resume requires an existing model.zip in the output directory")
    elif out.exists() and any(out.iterdir()):
        parser.error("Output directory is not empty. Select a new --output or use --resume")
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    config.update(seed=args.seed, repeat=args.repeat, requested_timesteps=timesteps)
    config["runtime"] = {"python": platform.python_version(), "torch": torch.__version__, "stable_baselines3": stable_baselines3.__version__, "gymnasium": gymnasium.__version__, "command": sys.argv}
    previous = json.loads((out / "config.json").read_text()) if args.resume else None
    if previous:
        if previous["seed"] != args.seed or previous["env_id"] != config["env_id"] or previous["ppo"] != config["ppo"]:
            parser.error("Resume must use the original seed, environment and PPO parameters")
        config["previous_timesteps"] = previous.get("actual_timesteps", 0)
    (out / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logs = out / (f"resume_{config['previous_timesteps']}" if args.resume else "training")
    env = make_vec_env(config["env_id"], n_envs=1, seed=args.seed, monitor_dir=str(logs))
    try:
        model = PPO.load(str(out / "model.zip"), env=env, device="cpu") if args.resume else PPO("MlpPolicy", env, seed=args.seed, device="cpu", verbose=0, **config["ppo"])
        model.set_logger(configure(str(logs), ["csv"]))
        callback = CheckpointCallback(save_freq=config["checkpoint_frequency"], save_path=str(out / "checkpoints"), name_prefix="ppo")
        model.learn(total_timesteps=timesteps, callback=callback, reset_num_timesteps=not args.resume)
        model.save(str(out / "model.zip"))
        config["actual_timesteps"] = model.num_timesteps
        (out / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        evaluate(model, config["env_id"], args.seed, config["eval_episodes"], out / "evaluation")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from stable_baselines3.common.monitor import load_results
        episodes = load_results(str(logs))
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.plot(episodes["l"].cumsum(), episodes["r"], color="#438675", alpha=0.5, linewidth=0.8)
        ax.plot(episodes["l"].cumsum(), episodes["r"].rolling(20, min_periods=1).mean(), color="#16724a", label="20-episode mean")
        ax.set(xlabel="Environment steps", ylabel="Episode return", title=f"PPO / {config['env_id']} / seed {args.seed}")
        ax.legend(); fig.tight_layout(); fig.savefig(out / "training.png", dpi=150); plt.close(fig)
        print(f"Training complete: {out.resolve()} ({model.num_timesteps} total steps)")
    finally:
        env.close()


if __name__ == "__main__":
    main()
