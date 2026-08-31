#!/usr/bin/env python3
"""Batch runner for GHOSTS: 7 lineups x {open, anonymous} x 3 runs = 42 games.

All games are launched in parallel (each is a separate ghosts.py process).
Every game gets its own explicit --log path under games/batch_<stamp>/ —
ghosts.py's default log name is timestamped to the second, which WOULD
collide across simultaneous launches, so the runner always passes --log.
Each game's console transcript goes to a matching .out file in the same
directory.

Usage:
    python run_batch.py           # real games (needs OPENROUTER_API_KEY)
    python run_batch.py --mock    # same 42-game matrix with scripted agents
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
HAIKU = "anthropic/claude-haiku-4.5"

# name -> models for agent_0..agent_5 (singletons seated at agent_0)
CONFIGS: dict[str, list[str]] = {
    "all-opus5":     [OPUS5] * 6,
    "all-sol":       [SOL] * 6,
    "1opus5-5sol":   [OPUS5] + [SOL] * 5,
    "1sol-5opus5":   [SOL] + [OPUS5] * 5,
    "3sol-3opus5":   [SOL] * 3 + [OPUS5] * 3,
    "1haiku-5opus5": [HAIKU] + [OPUS5] * 5,
    "5haiku-1opus5": [HAIKU] * 5 + [OPUS5],
}
ANONYMITY_MODES = ("open", "anonymous")
RUNS_PER_MODE = 3


def summarize(games: list[dict]) -> None:
    print("\n=== BATCH SUMMARY ===")
    model_wins: dict[str, int] = {}
    failures = 0
    for g in games:
        label = f"{g['config']:<14} {g['anonymity']:<10} run{g['run']}"
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
        except (OSError, StopIteration, KeyError, json.JSONDecodeError) as e:
            failures += 1
            print(f"  {label}  UNREADABLE LOG ({e}) — see {g['out_path']}")
            continue
        for m in winner_models:
            model_wins[m] = model_wins.get(m, 0) + 1
        winners = ", ".join(f"{w} ({m})" for w, m in zip(end["winners"], winner_models))
        print(f"  {label}  winners: {winners}")

    print("\nWinning seats by model (2 per completed game):")
    for model, count in sorted(model_wins.items(), key=lambda kv: -kv[1]):
        print(f"  {model}: {count}")
    if failures:
        print(f"\n{failures} of {len(games)} games failed — rerun those individually.")
        sys.exit(1)
    print(f"\nAll {len(games)} games completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GHOSTS batch matrix.")
    parser.add_argument("--mock", action="store_true",
                        help="run every game with scripted agents (no API calls)")
    args = parser.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPO_DIR, "games", f"batch_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    games: list[dict] = []
    for config, models in CONFIGS.items():
        for anonymity in ANONYMITY_MODES:
            for run in range(1, RUNS_PER_MODE + 1):
                name = f"{config}_{anonymity}_run{run}"
                log_path = os.path.join(out_dir, f"{name}.jsonl")
                out_path = os.path.join(out_dir, f"{name}.out")
                cmd = [sys.executable, GHOSTS,
                       "--anonymity", anonymity,
                       "--models", ",".join(models),
                       "--n-agents", "6",  # lineups are 6 seats; ignore .env N_AGENTS
                       "--log", log_path]
                if args.mock:
                    cmd.append("--mock")
                out_file = open(out_path, "w", encoding="utf-8")
                proc = subprocess.Popen(cmd, cwd=REPO_DIR, stdout=out_file,
                                        stderr=subprocess.STDOUT)
                games.append({
                    "config": config, "anonymity": anonymity, "run": run,
                    "log_path": log_path, "out_path": out_path,
                    "proc": proc, "out_file": out_file,
                })

    print(f"Launched {len(games)} games in parallel. Logs: {out_dir}")
    for g in games:
        g["returncode"] = g["proc"].wait()
        g["out_file"].close()
        status = "ok" if g["returncode"] == 0 else f"EXIT {g['returncode']}"
        print(f"  finished: {g['config']} {g['anonymity']} run{g['run']} [{status}]")

    summarize(games)


if __name__ == "__main__":
    main()
