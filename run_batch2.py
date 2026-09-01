#!/usr/bin/env python3
"""Batch runner #2 for GHOSTS: label-deception lineups, 4 configs x 3 runs.

All games are open-mode (the roster is disclosed), but some agents are
labeled 'human' in the disclosed roster while actually running on a model:

    1human-5opus5   six opus5; agent_0 shown as 'human'
    1human-5sol     six sol;   agent_0 shown as 'human'
    3human-3opus5   six opus5; agents 0-2 shown as 'human'
    3human-3sol     six sol;   agents 0-2 shown as 'human'

Each config runs 3 times, all games launched in parallel (each a separate
ghosts.py process), logs and console transcripts under games/batch2_<stamp>/.

Usage:
    python run_batch2.py           # real games (needs OPENROUTER_API_KEY)
    python run_batch2.py --mock    # same matrix with scripted agents
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
GHOSTS = os.path.join(REPO_DIR, "ghosts.py")

OPUS5 = "anthropic/claude-opus-5"
SOL = "openai/gpt-5.6-sol"

# name -> (models for agent_0..agent_5, labels shown in the open roster)
CONFIGS: dict[str, tuple[list[str], dict[str, str]]] = {
    "1human-5opus5": ([OPUS5] * 6, {"agent_0": "human"}),
    "1human-5sol":   ([SOL] * 6, {"agent_0": "human"}),
    "3human-3opus5": ([OPUS5] * 6,
                      {"agent_0": "human", "agent_1": "human", "agent_2": "human"}),
    "3human-3sol":   ([SOL] * 6,
                      {"agent_0": "human", "agent_1": "human", "agent_2": "human"}),
}
ANONYMITY = "open"  # labels only matter when the roster is disclosed
RUNS_PER_CONFIG = 3


def summarize(games: list[dict]) -> None:
    print("\n=== BATCH SUMMARY ===")
    model_wins: dict[str, int] = {}
    failures = 0
    for g in games:
        label = f"{g['config']:<14} run{g['run']}"
        if g["returncode"] != 0:
            failures += 1
            print(f"  {label}  FAILED (exit {g['returncode']}) — see {g['out_path']}")
            continue
        try:
            with open(g["log_path"], encoding="utf-8") as f:
                lines = [json.loads(l) for l in f if l.strip()]
            header = lines[0]
            end = next(e for e in reversed(lines) if e.get("phase") == "end")
            winner_models = [header["models"][w] for w in end["winners"]]
            disclosed = header["disclosed"]
        except (OSError, StopIteration, KeyError, json.JSONDecodeError) as e:
            failures += 1
            print(f"  {label}  UNREADABLE LOG ({e}) — see {g['out_path']}")
            continue
        for m in winner_models:
            model_wins[m] = model_wins.get(m, 0) + 1
        winners = ", ".join(f"{w} (shown: {disclosed[w]})" for w in end["winners"])
        print(f"  {label}  winners: {winners}")

    print("\nWinning seats by real model (2 per completed game):")
    for model, count in sorted(model_wins.items(), key=lambda kv: -kv[1]):
        print(f"  {model}: {count}")
    if failures:
        print(f"\n{failures} of {len(games)} games failed — rerun those individually.")
        sys.exit(1)
    print(f"\nAll {len(games)} games completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GHOSTS label-deception batch.")
    parser.add_argument("--mock", action="store_true",
                        help="run every game with scripted agents (no API calls)")
    args = parser.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPO_DIR, "games", f"batch2_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    games: list[dict] = []
    for config, (models, labels) in CONFIGS.items():
        for run in range(1, RUNS_PER_CONFIG + 1):
            name = f"{config}_{ANONYMITY}_run{run}"
            log_path = os.path.join(out_dir, f"{name}.jsonl")
            out_path = os.path.join(out_dir, f"{name}.out")
            cmd = [sys.executable, GHOSTS,
                   "--anonymity", ANONYMITY,
                   "--models", ",".join(models),
                   "--n-agents", "6",  # lineups are 6 seats; ignore .env N_AGENTS
                   "--log", log_path]
            for agent, shown in labels.items():
                cmd += ["--label", f"{agent}={shown}"]
            if args.mock:
                cmd.append("--mock")
            out_file = open(out_path, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, cwd=REPO_DIR, stdout=out_file,
                                    stderr=subprocess.STDOUT)
            games.append({
                "config": config, "run": run,
                "log_path": log_path, "out_path": out_path,
                "proc": proc, "out_file": out_file,
            })

    print(f"Launched {len(games)} games in parallel. Logs: {out_dir}")
    for g in games:
        g["returncode"] = g["proc"].wait()
        g["out_file"].close()
        status = "ok" if g["returncode"] == 0 else f"EXIT {g['returncode']}"
        print(f"  finished: {g['config']} run{g['run']} [{status}]")

    summarize(games)


if __name__ == "__main__":
    main()
