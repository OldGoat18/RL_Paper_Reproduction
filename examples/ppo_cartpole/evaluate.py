"""Independent seeded evaluation; results belong to this example project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.evaluation import evaluate_policy


def evaluate(model, env_id, seed, episodes, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    eval_seed = (seed + 10000) % 2**32
    env = make_vec_env(env_id, n_envs=1, seed=eval_seed)
    try:
        rewards, lengths = evaluate_policy(model, env, n_eval_episodes=episodes, deterministic=True, return_episode_rewards=True)
    finally:
        env.close()
    import numpy as np
    result = {"env_id": env_id, "training_seed": seed, "evaluation_seed": eval_seed, "episodes": episodes, "episode_returns": rewards, "episode_lengths": lengths, "mean_return": float(np.mean(rewards)), "std_return": float(np.std(rewards)), "deterministic_policy": True}
    (output / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mean_return": result["mean_return"], "episodes": episodes}))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--env", default="CartPole-v1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.episodes <= 0 or not 0 <= args.seed < 2**32:
        parser.error("Episodes must be positive and seed must be an unsigned 32-bit integer")
    torch.set_num_threads(1)
    model = PPO.load(str(args.model), device="cpu")
    evaluate(model, args.env, args.seed, args.episodes, args.output)


if __name__ == "__main__":
    main()
