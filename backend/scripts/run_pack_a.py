"""Headless Pack A driver.

Usage:
  python backend/scripts/run_pack_a.py --asr live  --llm live     # real Gemini ASR + agents
  python backend/scripts/run_pack_a.py --asr cached --llm replay   # recorded cassettes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Hinglish transcripts on a cp1252 Windows console otherwise raise UnicodeEncodeError
# halfway through a run, which looks like an agent failure and is not one.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from backend.adapters.asr import CachedASR, GeminiASR  # noqa: E402
from backend.kernel.llm import GeminiLLM  # noqa: E402
from backend.kernel.openrouter import OpenRouterLLM  # noqa: E402
from backend.kernel.schemas import Budget  # noqa: E402
from backend.kernel.trace import TraceRecorder  # noqa: E402
from backend.packs.creator.domain import Feed  # noqa: E402
from backend.packs.creator.session import CreatorSession, SessionConfig  # noqa: E402

FIX = ROOT / "backend" / "fixtures"
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
        print(f"    cites u={c['cited_utterance_ids']} v={c['cited_verdict_ids']} "
              f"e={c['cited_event_ids']} t={c['cited_tally_ids']} numbers={c['numbers']}")
    elif t == "tier0_skip":
        print(f"\n  TIER-0 SKIP {evt['utterance_id']}: {evt['reason']} (no LLM call)")
    elif t == "abstain":
        print(f"\n  ASR ABSTAIN {evt['utterance_id']}: conf={evt['confidence']:.2f} "
              f"transcript='{evt['transcript']}'")
    elif t == "prediction":
        p = evt["prediction"]
        print(f"\n  PREDICTION LOGGED {p['id']}: {p['bowler']} over {p['over']} "
              f"{p['metric']}>={p['threshold']}")
    elif t == "surface":
        if evt["mode"] != "card":
            print(f"    surfaced as {evt['mode']}: {evt['reason']}")
    elif t == "ask_user":
        print(f"\n  AGENT ASKS FAN: {evt['question']['text']} opts={evt['question']['options']}")
    elif t == "status":
        print(f"\n  STATUS: {evt['note']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["openrouter", "gemini", "cassette"],
                    default=os.getenv("LLM_BACKEND", "openrouter"))
    ap.add_argument("--asr", choices=["live", "cached"], default="cached")
    ap.add_argument("--llm", choices=["live", "replay", "record"], default="record")
    ap.add_argument("--only", default="", help="comma list of utterance ids")
    ap.add_argument("--queries", action="store_true")
    ap.add_argument("--budget", type=int, default=12)
    args = ap.parse_args()

    feed = Feed.load()
    rows = json.loads((FIX / "transcript_expected.json").read_text(encoding="utf-8"))
    for r in rows:
        r["audio_bytes"] = (FIX / r["wav"]).read_bytes()

    live = args.llm in ("live", "record") and args.backend != "cassette"
    mode = "record" if args.llm in ("live", "record") else "replay"
    if args.backend == "cassette":
        llm = OpenRouterLLM(live=False, cassette_mode="replay")
    elif args.backend == "gemini":
        llm = GeminiLLM(live=live, cassette_mode=mode)
    else:
        llm = OpenRouterLLM(live=live, cassette_mode=mode)

    # ASR always needs the Gemini backend: inline audio parts have no OpenRouter equivalent.
    # Its turns are cassetted too, so a live recording becomes the offline demo path.
    asr = GeminiASR(GeminiLLM(live=args.asr == "live",
                              cassette_mode="record" if args.asr == "live" else "replay")) \
        if args.asr == "live" else CachedASR()

    trace_path = OUT / f"pack_a_{args.asr}_{args.backend}_{args.llm}.jsonl"
    trace_path.unlink(missing_ok=True)
    trace = TraceRecorder(trace_path)
    sess = CreatorSession(llm, feed, asr, rows, trace, show,
                          cfg=SessionConfig(budget=_budget(args.budget)))

    wanted = set(args.only.split(",")) if args.only else None
    print(f"mode asr={args.asr} llm={args.llm} | feed events={len(feed.events)} | "
          f"segments={len(rows)}")
    for r in sorted(rows, key=lambda x: x["t_start"]):
        if wanted and r["id"] not in wanted:
            continue
        sess.advance(r["t_end"])
        print(f"\n=== {r['id']} @ {r['t_start']:.0f}s ({feed.state_at(r['t_end']) and feed.state_at(r['t_end']).over_label}) ===")
        print(f"    gold text: {r['text']}")
        sess.process(r)
    sess.advance(240.0)

    if args.queries:
        gold = json.loads((FIX / "gold_labels.json").read_text(encoding="utf-8"))
        for q in gold["queries"]:
            print(f"\n=== QUERY: {q['q']} ===")
            sess.run_query(q["q"])

    print(f"\n---- summary ----")
    print(f"cards committed: {len(sess.cards)}")
    for c in sess.cards:
        print(f"  {c.timestamp_label} [{c.truth_label}] {c.headline[:78]}")
    print(f"llm calls: {llm.calls} | trace events: {len(trace.events)} | "
          f"counts: {json.dumps(trace.counts())}")
    print(f"badges: {json.dumps(sess.badges())}")
    print(f"trace: {trace_path}")
    return 0


def _budget(n: int) -> Budget:
    # llm_calls is the real brake; a live Gemini turn can take ~20s, so the wall guard
    # has to be a demo SLA rather than a unit-test timeout.
    return Budget(max_llm_calls=n, max_tool_calls=12, max_ms=240_000,
                  max_specialist_rounds=8)


if __name__ == "__main__":
    sys.exit(main())
