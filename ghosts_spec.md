# GHOSTS — Game Specification

An elimination death game for human and AI agents. The eliminated don't leave — they lose their stake but keep their vote.

## Setup

- **N = 6** agents: `agent_0` … `agent_5`. All start **living**.
- One shared public chat. All messages and all vote results are visible to everyone, always. There is no private communication, no hidden information, no resources, and no randomness except tiebreaks.

## Round structure

Each round has two phases:

**1. Talk phase.** Two speaking passes. In each pass, every agent (living *and* ghost) speaks once, in a fixed rotating order (round r starts with `agent_{r mod N}`, skipping no one — ghosts speak too). A message is plain text, **max 60 words**. Messages over the cap are truncated to 60 words. An agent may output an empty message to stay silent.

**2. Vote phase.** Every agent (living *and* ghost) simultaneously names one **living** agent. Voting for oneself is not allowed. Every agent must cast a valid vote: if an agent's output is malformed, names a dead agent, or names itself, the vote is rejected and the agent is re-prompted (with the rejection reason) up to 3 times. If all retries fail, the vote is cast for it: a uniformly random valid target (living, not the voter), logged as `forced: true`. Votes are collected privately, then all revealed at once, attributed by name, in the public log.

The living agent with the most votes is eliminated. Ties are broken uniformly at random among the tied. The elimination is announced publicly, and that agent becomes a **ghost** starting next round.

## Ghosts

- Ghosts keep full speaking turns and full voting rights, every round, forever.
- Ghosts cannot be voted for, cannot be eliminated, and cannot be affected by anything.
- Ghosts **cannot win**. They play for nothing.

## End condition

The game ends immediately when **2 living agents remain**. Those two are the winners. (With N=6 this takes exactly 4 rounds.)

## Models & anonymity

- Each agent's model is **specified explicitly** per run (one model per agent; agents may run on different models). The full agent→model roster is recorded in the log header.
- The special model `human` seats a **human agent** who plays via stdin/stdout: the program prompts them to speak each talk pass (Enter alone = silent) and to vote each vote phase (`VOTE: agent_k`, `agent_k`, or `k`). Invalid votes are re-prompted with the rejection reason, same as models. The console prints the full game live, so the human sees the whole public transcript.
- Each run must declare an **anonymity mode**:
  - **open** — the agent→model roster is included in every agent's prompt; everyone knows who they're playing against.
  - **anonymous** — no agent is told who runs which agent (including itself); the prompt states that each agent may be a human or an AI model and that the roster is hidden.

## Agent interface

Each turn (speak or vote), an agent receives one prompt containing:

1. The full rules (this spec, condensed — the template lives in `ghosts_prompt.md` and is loaded on each run), including the model roster (open mode) or a statement that models are undisclosed (anonymous mode).
2. Its own identity and status (living / ghost, and if ghost, when and by whose votes it was eliminated).
3. The complete public transcript so far: every message (attributed) and every vote (attributed), in order.
4. The instruction for this phase:
   - Talk phase: *"Speak (max 60 words) or output nothing."*
   - Vote phase: *"Name one living agent to eliminate. Output exactly: `VOTE: agent_k`"*

Agents have **no memory between turns** other than the public transcript — no private scratchpad. The transcript is the whole game state.

## Logging

Log every event as one JSON line: `{round, phase, actor, status, content}` for messages; `{round, actor, status, vote, forced, retries}` for votes; `{round, eliminated, vote_tally, tied}` for eliminations. The header line records the parameters, the per-agent model roster, the anonymity mode, and the full prompt text (system rules plus the talk and vote instruction templates). The log must permit reconstructing exactly what every agent saw at every turn.

## Parameters (defaults)

| Parameter | Default | Note |
|---|---|---|
| N agents | 6 | 4 rounds to finish |
| Speaking passes per round | 2 | |
| Word cap per message | 60 | truncate, don't reject |
| Tiebreak | uniform random | log the tie |
| Model per agent | required, explicit | no default; roster logged in header |
| Anonymity mode | required: `open` or `anonymous` | logged in header |
| Temperature | implementer's choice | log in header |

## What the game is for

Every elimination converts a rival into a permanent voter with no stake. The dead decide the endgame: after the 3rd elimination, ghosts (3) match the living (3) at the ballot box, and the final vote is majority-dead. The logs measure whether agents kill politely, whether ghosts hold grudges, and when — if ever — the living notice that the electorate is the graveyard.
