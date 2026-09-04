# SquidGPT

A an elimination death game for AI and human agents, plus tooling to run batches
of games and analyze how models behave. The goal is to probe coalition behavior,
betrayal, lying, sectarianism, mercy, etc.

You can browse game transcripts using a convenient UI [here](https://claude.ai/code/artifact/7fad54d4-7144-4a4a-a801-cbc63e71e0ea).

See `./ghosts_prompt.md` for rules of the game. See `games/` for game
transcripts. Run `python ghosts.py --help` for details. The scripts
`run_batch.py` and `run_batch2.py` are just some scripts I generate to run a
bunch of games with different settings in parallel.
