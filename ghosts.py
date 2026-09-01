#!/usr/bin/env python3
"""GHOSTS — a social elimination game for LLM agents.

Implements ghosts_spec.md: N agents share one public chat. Each round has a
talk phase (2 passes, everyone speaks — living and ghosts) and a vote phase
(everyone votes for one living agent; the top vote-getter becomes a ghost).
Ghosts keep speaking and voting forever but cannot win. The game ends when
2 living agents remain.

Agents run via OpenRouter. Put your key in a .env file
(OPENROUTER_API_KEY=sk-or-...) or export it in the environment.

Every agent's model must be specified explicitly (one slug per agent, comma
separated), and every run must declare its anonymity mode: "open" means the
agent→model roster is disclosed to all agents in the prompt; "anonymous"
means no agent is told which model runs which agent (including itself).
In open mode, --label agent_k=LABEL (repeatable) replaces the model name
shown for that agent in the disclosed roster; unlabeled agents show their
real slug. The log header records both the real models and the labels.

The special slug "human" seats a human agent who plays over stdin/stdout:
the program prompts them to speak in talk phases and to vote in vote phases
(vote as `VOTE: agent_3`, `agent_3`, or just `3`; Enter alone = silent).
The console prints the whole game live, so human players see everything.

Usage:
    python ghosts.py --anonymity open \
        --models m0,m1,m2,m3,m4,m5   # real game (needs OPENROUTER_API_KEY)
    python ghosts.py --mock --anonymity anonymous   # scripted, no API calls
    python ghosts.py --seed 42 --effort high --anonymity open \
        --models anthropic/claude-opus-5,anthropic/claude-opus-5,openai/gpt-5.2,openai/gpt-5.2,google/gemini-3.1-pro,google/gemini-3.1-pro

Game parameters (agent count, talk passes per round, word cap) default to
N_AGENTS / SPEAKING_PASSES / WORD_CAP from the environment or .env file, and
can be overridden per run with --n-agents / --speaking-passes / --word-cap.
Unset everywhere, they fall back to the spec defaults (6 / 2 / 60).

The system prompt is loaded on each run from ghosts_prompt.md (next to this
script); edit that file to change what agents are told. Placeholders {n},
{n_minus_1}, {passes}, {cap}, and {model_disclosure} are filled in at game
start.

Every game writes a self-contained JSONL log to games/ghosts_<timestamp>.jsonl
(one JSON object per event; the header line records the per-agent models, the
anonymity mode, the full prompt text, and all parameters). No third-party
dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Parameters (spec defaults; overridable via .env / environment, then CLI)
# ---------------------------------------------------------------------------

DEFAULT_N_AGENTS = 6
DEFAULT_SPEAKING_PASSES = 2
DEFAULT_WORD_CAP = 60
MAX_VOTE_RETRIES = 3  # re-prompts after the initial attempt
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
HUMAN_MODEL = "human"  # model slug that seats a human agent on stdin/stdout

VOTE_RE = re.compile(r"VOTE:\s*(agent_\d+)")


def talk_instruction(word_cap: int) -> str:
    return f"Speak (max {word_cap} words) or output nothing."


VOTE_INSTRUCTION_TEMPLATE = (
    "Name one living agent to eliminate. Output exactly: `VOTE: agent_k`\n"
    "Living agents: {living}. You may not vote for yourself."
)

PROMPT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ghosts_prompt.md"
)


def load_rules_template() -> str:
    """Read the system-prompt template (placeholders: {n}, {n_minus_1},
    {passes}, {cap}, {model_disclosure})."""
    with open(PROMPT_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read().rstrip("\n")


def build_rules(disclosed: dict[str, str], anonymity: str,
                speaking_passes: int, word_cap: int) -> str:
    """Render the system prompt for this game from ghosts_prompt.md.

    anonymity == "open": the disclosed agent→name roster is part of the rules
    (per-agent labels may substitute for real model slugs).
    anonymity == "anonymous": agents are told the roster is hidden.
    """
    if anonymity == "open":
        # disclosed is insertion-ordered agent_0..agent_{n-1}; don't sort — the
        # lexical order puts agent_10 before agent_2.
        roster = "\n".join(f"    {agent}: {model}" for agent, model in disclosed.items())
        disclosure = (
            "- Who runs each agent is public knowledge. The roster:\n"
            + roster
        )
    else:
        disclosure = (
            "- Each agent may be a human or an AI model, and different agents\n"
            "  may be run by different players or models. Who runs which agent\n"
            "  (including you) is not disclosed to anyone."
        )
    n = len(disclosed)
    return load_rules_template().format(
        n=n, n_minus_1=n - 1, passes=speaking_passes,
        cap=word_cap, model_disclosure=disclosure,
    )


# ---------------------------------------------------------------------------
# .env support
# ---------------------------------------------------------------------------

def load_env() -> None:
    """Load KEY=VALUE pairs from a .env file (cwd, then the script's dir).

    Existing environment variables are never overridden.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                if key:
                    os.environ.setdefault(key, value)
        break  # first file found wins


# ---------------------------------------------------------------------------
# Agent backends
# ---------------------------------------------------------------------------

class OpenRouterBackend:
    """Drives every agent turn with a single stateless OpenRouter call."""

    def __init__(self, api_key: str, effort: str | None = None):
        self.api_key = api_key
        self.effort = effort

    def complete(self, prompt: str, ctx: dict, *, system: str, model: str) -> str:
        body = {
            "model": model,
            "max_tokens": 16000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        data = self._post(body)
        content = data["choices"][0]["message"].get("content") or ""
        return content.strip()

    def _post(self, body: dict, attempts: int = 5) -> dict:
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "GHOSTS",
        }
        last_error = None
        for attempt in range(attempts):
            if attempt:
                delay = min(2 ** attempt + random.uniform(0, 1), 30)
                print(f"    [retrying API call in {delay:.1f}s: {last_error}]",
                      file=sys.stderr)
                time.sleep(delay)
            try:
                req = urllib.request.Request(OPENROUTER_URL, data=payload,
                                             headers=headers)
                with urllib.request.urlopen(req, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if e.code in (408, 429) or e.code >= 500:
                    last_error = f"HTTP {e.code}: {detail}"
                    continue
                raise RuntimeError(f"OpenRouter error HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_error = repr(e)
                continue
            # OpenRouter can return 200 with an error object (e.g. provider issues)
            if "error" in data:
                last_error = f"API error: {data['error']}"
                continue
            return data
        raise RuntimeError(f"OpenRouter request failed after {attempts} attempts: {last_error}")

    def describe(self) -> dict:
        return {
            "provider": "openrouter",
            "temperature": None,  # not sent; model default
            "reasoning_effort": self.effort or "model default (adaptive)",
        }


class MockBackend:
    """Deterministic scripted agents for testing the engine without API calls."""

    def complete(self, prompt: str, ctx: dict, *, system: str = "",
                 model: str = "mock") -> str:
        actor, phase = ctx["actor"], ctx["phase"]
        if phase == "vote":
            targets = [a for a in ctx["living"] if a != actor]
            return f"VOTE: {min(targets)}" if targets else ""
        idx = int(actor.split("_")[1])
        if ctx["status"] == "ghost":
            return f"From the grave, {actor} watches round {ctx['round']}."
        if idx % 3 == 2:
            return ""  # exercise the silent path
        return f"{actor} here in round {ctx['round']}, pass {ctx['pass']}. Stay alert."

    def describe(self) -> dict:
        return {"provider": "mock", "temperature": None}


class HumanBackend:
    """Plays an agent through the terminal: prompts on stdout, reads stdin.

    The console already prints every message, vote, tally, and elimination
    live, so the human sees the full public transcript as the game goes; each
    turn only shows their identity, the instruction, and any rejection reason.
    """

    def complete(self, prompt: str, ctx: dict, *, system: str = "",
                 model: str = HUMAN_MODEL) -> str:
        actor, status, phase = ctx["actor"], ctx["status"], ctx["phase"]
        where = f"round {ctx['round']}"
        if phase == "talk":
            where += f", talk pass {ctx['pass']}"
        print(f"\n>>> {actor}: YOUR turn ({status}, {where}).")
        if ctx.get("rejection"):
            print(f">>> Your previous input was rejected: {ctx['rejection']}")
        for line in ctx["instruction"].splitlines():
            print(f">>> {line}")
        try:
            raw = input(">>> ").strip()
        except EOFError:
            sys.exit(f"\nstdin closed while waiting for {actor}'s input — aborting.")
        if phase == "vote":  # accept "3" or "agent_3" as shorthand
            if re.fullmatch(r"\d+", raw):
                raw = f"agent_{raw}"
            if re.fullmatch(r"agent_\d+", raw):
                raw = f"VOTE: {raw}"
        return raw

    def describe(self) -> dict:
        return {"provider": "human", "temperature": None}


class RoutingBackend:
    """Sends each turn to the human terminal or the model backend by slug."""

    def __init__(self, model_backend):
        self.model_backend = model_backend
        self.human_backend = HumanBackend()

    def complete(self, prompt: str, ctx: dict, *, system: str, model: str) -> str:
        backend = self.human_backend if model == HUMAN_MODEL else self.model_backend
        return backend.complete(prompt, ctx, system=system, model=model)

    def describe(self) -> dict:
        return (self.model_backend or self.human_backend).describe()


# ---------------------------------------------------------------------------
# Game engine
# ---------------------------------------------------------------------------

class GhostsGame:
    def __init__(self, backend, models: list[str], anonymity: str,
                 log_path: str, seed: int | None = None,
                 speaking_passes: int = DEFAULT_SPEAKING_PASSES,
                 word_cap: int = DEFAULT_WORD_CAP,
                 labels: dict[str, str] | None = None):
        if len(models) < 3:
            raise ValueError(f"need at least 3 agents, got {len(models)}")
        if anonymity not in ("open", "anonymous"):
            raise ValueError(f"anonymity must be 'open' or 'anonymous', got {anonymity!r}")
        if speaking_passes < 1 or word_cap < 1:
            raise ValueError("speaking_passes and word_cap must be >= 1")
        self.backend = backend
        self.rng = random.Random(seed)
        self.n_agents = len(models)
        self.speaking_passes = speaking_passes
        self.word_cap = word_cap
        self.talk_instruction = talk_instruction(word_cap)
        self.agents = [f"agent_{i}" for i in range(self.n_agents)]
        self.models = dict(zip(self.agents, models))
        self.anonymity = anonymity
        self.labels = dict(labels or {})
        unknown = sorted(set(self.labels) - set(self.agents))
        if unknown:
            raise ValueError(f"labels for unknown agents: {', '.join(unknown)}")
        # What the open-mode roster shows: the label if one was given, else the
        # real model slug. Anonymous mode discloses nothing either way.
        self.disclosed = {a: self.labels.get(a, m) for a, m in self.models.items()}
        self.rules = build_rules(self.disclosed, anonymity, speaking_passes, word_cap)
        self.living = list(self.agents)
        # ghost name -> {"round": int, "voters": [names]}
        self.ghosts: dict[str, dict] = {}
        self.transcript_lines: list[str] = []
        self.log_file = open(log_path, "w", encoding="utf-8")
        self.log({
            "header": True,
            "game": "GHOSTS",
            "started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "n_agents": self.n_agents,
            "speaking_passes": speaking_passes,
            "word_cap": word_cap,
            "seed": seed,
            "models": self.models,
            "labels": self.labels,
            "disclosed": self.disclosed,
            "anonymity": anonymity,
            "prompt": {
                "system": self.rules,
                "talk_instruction": self.talk_instruction,
                "vote_instruction_template": VOTE_INSTRUCTION_TEMPLATE,
            },
            **backend.describe(),
        })

    # -- logging ------------------------------------------------------------

    def log(self, event: dict) -> None:
        self.log_file.write(json.dumps(event) + "\n")
        self.log_file.flush()

    def status_of(self, agent: str) -> str:
        return "living" if agent in self.living else "ghost"

    # -- prompt construction ------------------------------------------------

    def identity(self, agent: str) -> str:
        if agent in self.living:
            return f"You are {agent}. Status: LIVING."
        info = self.ghosts[agent]
        voters = ", ".join(info["voters"]) or "nobody (tiebreak)"
        return (
            f"You are {agent}. Status: GHOST — eliminated in round "
            f"{info['round']} by votes from {voters}. You cannot win; "
            f"you play for nothing, but you still speak and vote every round."
        )

    def build_prompt(self, agent: str, instruction: str) -> str:
        transcript = "\n".join(self.transcript_lines) or "(empty — the game is just beginning)"
        return (
            f"{self.identity(agent)}\n\n"
            f"PUBLIC TRANSCRIPT SO FAR:\n{transcript}\n\n"
            f"{instruction}"
        )

    # -- talk phase ---------------------------------------------------------

    def speaking_order(self, round_num: int) -> list[str]:
        start = round_num % self.n_agents
        return [self.agents[(start + i) % self.n_agents] for i in range(self.n_agents)]

    def talk_phase(self, round_num: int) -> None:
        order = self.speaking_order(round_num)
        for pass_num in range(1, self.speaking_passes + 1):
            self.transcript_lines.append(
                f"--- Round {round_num}, talk pass {pass_num} ---"
            )
            for agent in order:
                status = self.status_of(agent)
                ctx = {
                    "actor": agent, "phase": "talk", "status": status,
                    "round": round_num, "pass": pass_num, "living": self.living,
                    "instruction": self.talk_instruction,
                }
                prompt = self.build_prompt(agent, self.talk_instruction)
                raw = self.backend.complete(prompt, ctx, system=self.rules,
                                            model=self.models[agent])
                message = truncate_words(raw, self.word_cap)
                self.log({
                    "round": round_num,
                    "phase": f"talk_{pass_num}",
                    "actor": agent,
                    "status": status,
                    "content": message,
                })
                if message:
                    self.transcript_lines.append(f'{agent} ({status}): "{message}"')
                    print(f'  {agent} ({status}): {message}')
                else:
                    self.transcript_lines.append(f"{agent} ({status}): [silent]")
                    print(f"  {agent} ({status}): [silent]")

    # -- vote phase ---------------------------------------------------------

    def validate_vote(self, actor: str, raw: str) -> tuple[str | None, str]:
        """Return (target, "") if valid, else (None, rejection_reason)."""
        matches = VOTE_RE.findall(raw)
        if not matches:
            return None, "malformed output — it must contain exactly 'VOTE: agent_k'"
        target = matches[-1]
        if target not in self.agents:
            return None, f"{target} does not exist"
        if target == actor:
            return None, "you may not vote for yourself"
        if target not in self.living:
            return None, f"{target} is a ghost and cannot be voted for"
        return target, ""

    def collect_vote(self, actor: str, round_num: int) -> dict:
        instruction = VOTE_INSTRUCTION_TEMPLATE.format(living=", ".join(self.living))
        base_prompt = self.build_prompt(actor, instruction)
        ctx = {
            "actor": actor, "phase": "vote", "status": self.status_of(actor),
            "round": round_num, "living": self.living,
            "instruction": instruction,
        }
        prompt = base_prompt
        for attempt in range(1 + MAX_VOTE_RETRIES):
            raw = self.backend.complete(prompt, ctx, system=self.rules,
                                        model=self.models[actor])
            target, reason = self.validate_vote(actor, raw)
            if target is not None:
                return {"vote": target, "forced": False, "retries": attempt}
            ctx = {**ctx, "rejection": f"{reason} (you said: {raw!r})"}
            prompt = (
                f"{base_prompt}\n\n"
                f"Your previous output was rejected: {reason}.\n"
                f"Your previous output was: {raw!r}\n"
                f"Output exactly: VOTE: agent_k"
            )
        forced_target = self.rng.choice([a for a in self.living if a != actor])
        return {"vote": forced_target, "forced": True, "retries": MAX_VOTE_RETRIES}

    def vote_phase(self, round_num: int) -> None:
        # Collected privately: no vote enters the transcript until all are in.
        results = {agent: self.collect_vote(agent, round_num) for agent in self.agents}

        self.transcript_lines.append(f"--- Round {round_num}, votes (all revealed at once) ---")
        for agent in self.agents:
            r = results[agent]
            status = self.status_of(agent)
            self.log({
                "round": round_num,
                "actor": agent,
                "status": status,
                "vote": r["vote"],
                "forced": r["forced"],
                "retries": r["retries"],
            })
            forced_note = " [forced: invalid after retries]" if r["forced"] else ""
            self.transcript_lines.append(
                f"{agent} ({status}) voted for {r['vote']}{forced_note}"
            )
            print(f"  {agent} ({status}) → {r['vote']}{forced_note}")

        tally: dict[str, int] = {}
        for r in results.values():
            tally[r["vote"]] = tally.get(r["vote"], 0) + 1
        top = max(tally.values())
        tied = sorted(a for a, c in tally.items() if c == top)
        eliminated = self.rng.choice(tied)

        self.log({
            "round": round_num,
            "eliminated": eliminated,
            "vote_tally": tally,
            "tied": tied if len(tied) > 1 else [],
        })
        tally_str = ", ".join(f"{a}: {c}" for a, c in sorted(tally.items()))
        self.transcript_lines.append(f"Tally: {tally_str}")
        if len(tied) > 1:
            self.transcript_lines.append(
                f"Tie among {', '.join(tied)} — broken uniformly at random."
            )
        self.transcript_lines.append(
            f"{eliminated} is ELIMINATED and becomes a ghost starting next round."
        )
        print(f"  Tally: {tally_str}")
        print(f"  ELIMINATED: {eliminated}" + (f" (tie among {', '.join(tied)})" if len(tied) > 1 else ""))

        self.living.remove(eliminated)
        self.ghosts[eliminated] = {
            "round": round_num,
            "voters": [a for a in self.agents if results[a]["vote"] == eliminated],
        }

    # -- main loop ----------------------------------------------------------

    def run(self) -> list[str]:
        round_num = 0
        while len(self.living) > 2:
            round_num += 1
            print(f"\n=== Round {round_num} "
                  f"(living: {', '.join(self.living)} | "
                  f"ghosts: {', '.join(self.ghosts) or 'none'}) ===")
            self.talk_phase(round_num)
            self.vote_phase(round_num)
        winners = sorted(self.living)
        self.log({"round": round_num, "phase": "end", "winners": winners})
        print(f"\n=== GAME OVER — winners: {', '.join(winners)} ===")
        self.log_file.close()
        return winners


def truncate_words(text: str, cap: int) -> str:
    words = text.split()
    return " ".join(words[:cap])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def default_log_path() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("games", exist_ok=True)
    return os.path.join("games", f"ghosts_{stamp}.jsonl")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        sys.exit(f"{name} in the environment must be an integer, got {value!r}")


def main() -> None:
    load_env()  # .env supplies parameter defaults (and the API key later)

    parser = argparse.ArgumentParser(description="Run a game of GHOSTS.")
    parser.add_argument("--mock", action="store_true",
                        help="use scripted agents instead of the OpenRouter API")
    parser.add_argument("--models", default=None,
                        help="comma-separated OpenRouter model slugs, one per agent "
                             "(exactly n_agents, in agent_0..agent_{n-1} order); "
                             "required unless --mock")
    parser.add_argument("--anonymity", required=True,
                        choices=["open", "anonymous"],
                        help="'open': every agent is told which model runs each agent; "
                             "'anonymous': the roster is hidden from all agents")
    parser.add_argument("--label", action="append", default=[],
                        metavar="agent_k=LABEL",
                        help="in the open-mode roster, show LABEL for agent_k "
                             "instead of its real model slug (repeatable; other "
                             "agents keep their real slugs; no effect on "
                             "anonymous games)")
    parser.add_argument("--n-agents", type=int, default=None,
                        help="number of agents (default: N_AGENTS from the "
                             f"environment/.env, else {DEFAULT_N_AGENTS})")
    parser.add_argument("--speaking-passes", type=int, default=None,
                        help="talk passes per round (default: SPEAKING_PASSES from "
                             f"the environment/.env, else {DEFAULT_SPEAKING_PASSES})")
    parser.add_argument("--word-cap", type=int, default=None,
                        help="max words per message (default: WORD_CAP from the "
                             f"environment/.env, else {DEFAULT_WORD_CAP})")
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high"],
                        help="reasoning effort per agent turn (default: model default)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for tiebreaks and forced votes")
    parser.add_argument("--log", default=None,
                        help="output JSONL log path (default: games/ghosts_<timestamp>.jsonl)")
    args = parser.parse_args()

    # Resolution order: CLI flag > environment (.env) > spec default.
    n_agents = args.n_agents if args.n_agents is not None else env_int("N_AGENTS", DEFAULT_N_AGENTS)
    speaking_passes = (args.speaking_passes if args.speaking_passes is not None
                       else env_int("SPEAKING_PASSES", DEFAULT_SPEAKING_PASSES))
    word_cap = args.word_cap if args.word_cap is not None else env_int("WORD_CAP", DEFAULT_WORD_CAP)
    if n_agents < 3:
        parser.error("n_agents must be at least 3 (the game ends at 2 living)")
    if speaking_passes < 1 or word_cap < 1:
        parser.error("speaking_passes and word_cap must be at least 1")

    if args.models is not None:
        models = [m.strip() for m in args.models.split(",")]
        if len(models) != n_agents or not all(models):
            parser.error(f"--models must list exactly {n_agents} non-empty "
                         f"model slugs, comma-separated (got {len(models)})")
    elif args.mock:
        models = ["mock"] * n_agents
    else:
        parser.error("--models is required (one model slug per agent) "
                     "unless --mock is given")

    labels: dict[str, str] = {}
    for spec in args.label:
        agent, sep, label = spec.partition("=")
        agent, label = agent.strip(), label.strip()
        if not sep or not label or not re.fullmatch(r"agent_\d+", agent):
            parser.error(f"--label must be agent_k=LABEL, got {spec!r}")
        if int(agent.split("_")[1]) >= n_agents:
            parser.error(f"--label names {agent}, but there are only "
                         f"{n_agents} agents (agent_0..agent_{n_agents - 1})")
        if agent in labels:
            parser.error(f"duplicate --label for {agent}")
        labels[agent] = label
    if labels and args.anonymity != "open":
        print("warning: --label has no effect in anonymous mode "
              "(the roster is never disclosed)", file=sys.stderr)

    if args.mock:
        model_backend = MockBackend()
    elif any(m != HUMAN_MODEL for m in models):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            sys.exit("OPENROUTER_API_KEY is not set. Put it in a .env file "
                     "(OPENROUTER_API_KEY=sk-or-...) or export it.")
        model_backend = OpenRouterBackend(api_key, effort=args.effort)
    else:
        model_backend = None  # every agent is human; no API needed
    backend = RoutingBackend(model_backend)

    log_path = args.log or default_log_path()
    game = GhostsGame(backend, models=models, anonymity=args.anonymity,
                      log_path=log_path, seed=args.seed,
                      speaking_passes=speaking_passes, word_cap=word_cap,
                      labels=labels)
    game.run()
    print(f"\nFull log written to {log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
