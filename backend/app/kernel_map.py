"""The honest org chart of the running kernel, read out of the code that runs it.

The UI animates a supervisor dispatching specialists. That animation is only worth watching if
the roster on screen is the roster the kernel actually has - so every field here is introspected
from `build_agents()`, the capability-scoped tool registries and the supervisor contract string.
Nothing in this module is written by hand from memory, and nothing here is a claim about
behaviour: it is a map of what is registered.

Toolboxes are constructed with `session=None` and only their registry is read. Building a
registry wraps bound methods and never calls them, so this cannot touch a feed or an LLM.
"""
from __future__ import annotations

from typing import Any


def _supervisor_contract_lines(limit: int = 3) -> list[str]:
    from ..kernel.orchestrator import SUP_CONTRACT

    lines = [ln.strip() for ln in SUP_CONTRACT.strip().splitlines() if ln.strip()]
    return lines[:limit]


def _pack(pack: str) -> dict[str, Any]:
    if pack == "gap":
        from ..packs.gap.agents import build_agents
        from ..packs.gap.tools import GapToolbox as Toolbox
        supervisor_caps = ["telemetry", "past_events", "video_evidence", "ask_user", "permission"]
    elif pack == "creator":
        from ..packs.creator.agents import build_agents
        from ..packs.creator.tools import CreatorToolbox as Toolbox
        supervisor_caps = []          # creator pack inherits the kernel default (no caps)
    else:
        raise ValueError(f"unknown pack {pack!r}")

    agents = build_agents()
    registry = Toolbox(None).build_registry()
    tools = registry._tools                                   # read-only introspection
    return {
        "agents": [
            {"role": spec.role, "mission": spec.mission,
             "tools": sorted(spec.tools), "capabilities": sorted(spec.capabilities),
             "max_inner_calls": spec.max_inner_calls}
            for spec in agents.values()
        ],
        "tools": [
            {"name": t.name, "description": t.description,
             "capabilities": list(t.capabilities),
             "deterministic": t.deterministic, "read_only": t.read_only}
            for t in tools.values()
        ],
        "supervisor_capabilities": supervisor_caps,
        "critic_required_before_commit": True,
    }


def kernel_map() -> dict[str, Any]:
    """Everything the agent-room animation needs, and nothing it would have to invent."""
    return {
        "supervisor": {
            "role": "supervisor",
            "mission": "choose the next single step: dispatch a specialist, run a tool, ask the "
                       "fan, request permission, commit, skip or stop",
            "contract": _supervisor_contract_lines(),
            "cannot": ["compute a score", "label truth", "write a number a tool did not return"],
        },
        "packs": {name: _pack(name) for name in ("gap", "creator")},
        "walls": [
            {"id": "capability_scoping", "where": "kernel/registry.py",
             "claim": "an agent only receives the tools and capabilities its role grants"},
            {"id": "clamp", "where": "kernel/clamp.py",
             "claim": "the feed is a ClampedFeed; a ball at or after the fan's cursor has no text"},
            {"id": "digit_gate", "where": "kernel/commit.py",
             "claim": "every number in a card must appear in the evidence the gate licensed"},
            {"id": "mandatory_check", "where": "packs/*/session.py",
             "claim": "pack hooks verify deterministically before any model opinion counts"},
            {"id": "critic_wall", "where": "kernel/orchestrator.py",
             "claim": "a card cannot commit without the critic role having run"},
            {"id": "budget", "where": "kernel/schemas.py",
             "claim": "LLM calls, tool calls, specialist rounds, steps and wall milliseconds are "
                      "hard-capped; hitting a cap is reported, never hidden"},
        ],
    }
