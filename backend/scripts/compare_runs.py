"""Prove that a cassette replay is the SAME run as the live recording, not a fallback.

`--llm off` and `--llm replay` can both print three PASS lines. Only this distinction matters
to a reviewer: in a replay the committed cards must be the cards the live model produced, and
the trace must show model turns rather than the deterministic fallback doing the work.

Usage: python backend/scripts/compare_runs.py record replay
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "backend" / "out"


def load(backend: str, mode: str) -> list[dict]:
    p = OUT / f"pack_b_{backend}_{mode}.jsonl"
    if not p.exists():
        raise SystemExit(f"no trace at {p} - run: python backend/scripts/run_pack_b.py --llm {mode}")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def cards(rows: list[dict]) -> list[dict]:
    """The journal stores the committed draft, not a `card` event."""
    return [r["draft"] for r in rows if r.get("kind") == "commit" and r.get("ok")]


def main() -> int:
    backend = sys.argv[1] if len(sys.argv) > 1 else "openrouter"
    a = sys.argv[2] if len(sys.argv) > 2 else "record"
    b = sys.argv[3] if len(sys.argv) > 3 else "replay"
    ra, rb = load(backend, a), load(backend, b)
    ca, cb = cards(ra), cards(rb)

    kinds_a = Counter(r["kind"] for r in ra)
    kinds_b = Counter(r["kind"] for r in rb)
    print(f"{a}: {len(ca)} cards | {len(ra)} events | kinds={dict(kinds_a)}")
    print(f"{b}: {len(cb)} cards | {len(rb)} events | kinds={dict(kinds_b)}")

    fails: list[str] = []
    if [c.get("body") for c in ca] != [c.get("body") for c in cb]:
        fails.append("card bodies differ between the two runs")
    if [c.get("headline") for c in ca] != [c.get("headline") for c in cb]:
        fails.append("card headlines differ")
    if [c.get("cited_event_ids") for c in ca] != [c.get("cited_event_ids") for c in cb]:
        fails.append("cited evidence differs")

    # The provenance test: a live/replay run whose cards came from the deterministic fallback
    # is not a demonstration of the agent. `status` notes are how the kernel announces that.
    fb = [r for r in rb if r["kind"] == "status" and "deterministic" in str(r.get("note", ""))]
    decisions = kinds_b.get("supervisor_decision", 0)
    tools = kinds_b.get("tool_call", 0)
    if fb:
        fails.append(f"{len(fb)} card(s) came from the deterministic fallback, not the model path")
    if not decisions:
        fails.append("no supervisor_decision events: the model never chose a step")
    if not tools:
        fails.append("no tool_call events: no agent read any evidence")

    for c in cb:
        print(f"  {c.get('kind',''):8s} {c.get('headline')}")

    if fails:
        print("MISMATCH:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"MATCH: {len(cb)} identical cards, {decisions} model turns, {tools} tool calls, "
          "0 fallback commits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
