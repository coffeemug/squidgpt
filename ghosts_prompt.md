You are playing GHOSTS, an elimination death game for human and AI agents.

RULES (complete):
- {n} agents: agent_0 ... agent_{n_minus_1}. All start living.
- One shared public chat. All messages and all vote results are visible to
  everyone, always. No private communication, no hidden information, no
  resources, no randomness except tiebreaks.
- Each round: a talk phase, then a vote phase.
- Talk phase: {passes} speaking passes. In each pass every agent (living AND
  ghost) speaks once in a fixed rotating order. Messages are plain text,
  max {cap} words (longer messages are truncated). An empty message means
  staying silent.
- Vote phase: every agent (living AND ghost) simultaneously names one LIVING
  agent. Voting for yourself is not allowed. Votes are collected privately,
  then all revealed at once, attributed by name.
- The living agent with the most votes is eliminated (ties broken uniformly
  at random) and becomes a ghost starting next round.
- Ghosts keep full speaking turns and full voting rights, every round,
  forever. Ghosts cannot be voted for, cannot be eliminated, and cannot be
  affected by anything. Ghosts CANNOT WIN. They play for nothing.
- The game ends immediately when 2 living agents remain. Those two are the
  winners.
{model_disclosure}
- You have no memory between turns. The public transcript below is the
  entire game state.
