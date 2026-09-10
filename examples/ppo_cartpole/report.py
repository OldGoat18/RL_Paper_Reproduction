"""This project, not the harness, interprets its own evaluation schema."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args()
    rows = []
    for path in sorted(args.runs.glob("seed_*/repeat_*/evaluation/evaluation.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"path": str(path), "seed": value["training_seed"], "repeat": path.parents[1].name, "mean_return": value["mean_return"], "episodes": value["episodes"]})
    if not rows:
        parser.error("No completed evaluations found")
    by_seed = {}
    for row in rows:
        by_seed.setdefault(row["seed"], []).append(row["mean_return"])
    means = [statistics.mean(values) for values in by_seed.values()]
    result = {"runs": rows, "independent_seeds": len(means), "mean_across_seeds": statistics.mean(means), "sample_std_across_seeds": statistics.stdev(means) if len(means) > 1 else None}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = ["# PPO / CartPole Baseline", "", f"Independent seeds: {len(means)}", f"Mean return across seeds: {result['mean_across_seeds']:.2f}", "", "| Seed | Repeat | Mean Return | Episodes |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {r['seed']} | {r['repeat']} | {r['mean_return']:.2f} | {r['episodes']} |" for r in rows)
    lines.extend(["", "Repeated runs of the same seed are averaged before cross-seed statistics.", "This is a lightweight implementation baseline, not validation of the paper's original benchmark results."])
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
