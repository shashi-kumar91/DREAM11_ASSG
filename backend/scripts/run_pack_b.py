"""Headless Pack B driver: three golden gap scenarios, replayed and checked.

Usage:
  python backend/scripts/run_pack_b.py --llm off      # deterministic receipts, zero model calls
  python backend/scripts/run_pack_b.py --llm record   # live provider, records cassettes
  python backend/scripts/run_pack_b.py --llm replay   # recorded cassettes only

Every run is scored against the fixture's `expected` block: did the agent ask when asking
had value, did it stay quiet when it did not, did the receipt carry the right balls and none
after the fan's position. The checks are printed as PASS/FAIL lines so a regression is a
non-zero exit, not a vibe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from backend.kernel.llm import GeminiLLM, UnavailableLLM  # noqa: E402
from backend.kernel.openrouter import OpenRouterLLM  # noqa: E402
from backend.kernel.schemas import Budget  # noqa: E402
from backend.kernel.trace import TraceRecorder  # noqa: E402
from backend.packs.gap import boundary as B  # noqa: E402
from backend.packs.gap.domain import GapFixtures  # noqa: E402
from backend.packs.gap.session import GapConfig, GapSession  # noqa: E402

OUT = ROOT / "backend" / "out"
OUT.mkdir(exist_ok=True)


def show(evt: dict) -> None:
    t = evt.get("type")
    if t == "card":
        c = evt["card"]
        print(f"\n  CARD [{c['truth_label']}] kind={c['kind']} tmpl={c['template_used']}")
        print(f"    {c['headline']}")
        for line in c["body"].splitlines():
            print(f"    {line}")
        print(f"    cites e={c['cited_event_ids']} t={c['cited_tally_ids']} "
              f"numbers={c['numbers']}")
    elif t == "ask_user":
        q = evt["question"]
        print(f"\n  AGENT ASKS FAN: {q['text']}")
        print(f"    options: {q['options']}")
    elif t == "status":
        print(f"\n  STATUS: {evt['note']}")


def _budget(n: int) -> Budget:
    return Budget(max_llm_calls=n, max_tool_calls=12, max_ms=240_000,
                  max_specialist_rounds=8)


def check(scenario_id: str, sess: GapSession, expected: dict) -> list[str]:
    """Score the run against the fixture. Returns the failing assertions."""
    fails: list[str] = []
    asked = any(e["kind"] == "ask_user" for e in sess.trace.events)
    if expected.get("asks") is not None and asked != expected["asks"]:
        fails.append(f"asks: expected {expected['asks']}, got {asked}")

    cards = [c for c in sess.cards]
    kinds = [c.kind for c in cards]
    if expected.get("modality"):
        want = {"no_op": "noop", "receipt": "receipt", "jump_to_live": "status"}[expected["modality"]]
        if want not in kinds:
            fails.append(f"modality: expected a {want} card, got {kinds or 'nothing'}")
    if not cards:
        fails.append("no card committed")

    cited = {e for c in cards for e in c.cited_event_ids}
    for eid in expected.get("material_must_include", []):
        if eid not in cited:
            fails.append(f"missing material event {eid} "
                         f"({sess.feed.by_id[eid].over_label}) in the receipt citations")
    for eid in expected.get("must_exclude", []):
        if eid in cited:
            fails.append(f"spoiler: cited {eid} ({sess.feed.by_id[eid].over_label}), "
                         f"which is at or past the fan's position")
        if eid in sess.released_events:
            fails.append(f"leak: {eid} was released to an agent past the resume cursor")
    if expected.get("boundary"):
        if sess.chosen_boundary != expected["boundary"]:
            fails.append(f"boundary: expected {expected['boundary']} "
                         f"({sess.feed.by_id[expected['boundary']].over_label}), got "
                         f"{sess.chosen_boundary or '-'} from {sess.boundary_source}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["openrouter", "gemini", "cassette"], default="openrouter")
    ap.add_argument("--llm", choices=["live", "record", "replay", "off"], default="off")
    ap.add_argument("--only", default="", help="comma list of scenario ids")
    ap.add_argument("--budget", type=int, default=14)
    args = ap.parse_args()

    fixtures = GapFixtures.load()
    print(f"pack B | feed events={len(fixtures.feed.events)} | "
          f"frames={len(fixtures.frames)} | scenarios={list(fixtures.scenarios)}")

    if args.llm == "off":
        # Dead transport, not live=False on a real one: a replaying cassette still turns 34
        # recorded model turns into a card, which is the layer this flag must not claim.
        llm = UnavailableLLM()
    elif args.backend == "gemini":
        llm = GeminiLLM(live=True, cassette_mode="record" if args.llm != "replay" else "replay")
    elif args.llm == "replay":
        llm = OpenRouterLLM(live=False, cassette_mode="replay")
    else:
        llm = OpenRouterLLM(live=True, cassette_mode="record")

    wanted = set(args.only.split(",")) if args.only else None
    trace_path = OUT / f"pack_b_{args.backend}_{args.llm}.jsonl"
    trace_path.unlink(missing_ok=True)
    failures: dict[str, list[str]] = {}
    all_cards = 0
    all_events = 0

    for sid, sc in fixtures.scenarios.items():
        if wanted and sid not in wanted:
            continue
        trace = TraceRecorder(trace_path)
        # An empty model budget is what makes "no model calls" measurable rather than hopeful:
        # the kernel walls at step one, so the receipt on screen is the deterministic fallback's.
        sess = GapSession(llm, fixtures, trace, show,
                          cfg=GapConfig(budget=_budget(0 if args.llm == "off" else args.budget)),
                          auto_answer=sc.fan_answers)
        print(f"\n================ {sid}: {sc.name} ================")
        print(f"  {sc.blurb}")
        b = B.estimate(fixtures.feed, sc)
        print(f"  boundary candidates={b.labels(fixtures.feed)} conf={b.confidence}")
        for n in b.notes:
            print(f"    - {n}")
        sess.run_scenario(sid)
        all_cards += len(sess.cards)
        all_events += len(trace.events)
        fails = check(sid, sess, sc.expected)
        failures[sid] = fails
        for f in fails:
            print(f"  FAIL {f}")
        if not fails:
            print(f"  PASS ({len(sess.cards)} card(s), {llm.calls} llm calls)")

    print("\n---- summary ----")
    print(f"cards committed: {all_cards} | llm calls: {llm.calls} | "
          f"trace events: {all_events} | badges: {sess.badges()}")
    bad = {k: v for k, v in failures.items() if v}
    for sid, v in failures.items():
        print(f"  {sid}: {'FAIL ' + '; '.join(v) if v else 'pass'}")
    print(f"trace: {trace_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
