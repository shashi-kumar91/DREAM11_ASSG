"""Which free-tier route can actually drive this kernel?

The supervisor turn is the one that decides whether the episode does anything, so a model is
only useful here if it returns ONE object that validates as `SupervisorDecision` on the first
try. This script sends the same realistic turn to every candidate and prints latency, output
tokens and whether the decision parsed - the evidence behind `OPENROUTER_MODEL_CHAIN`.

Usage: python backend/scripts/bench_models.py [--turn supervisor|specialist] [--models a,b]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

from backend.kernel.orchestrator import SUP_CONTRACT, SpecialistTurn  # noqa: E402
from backend.kernel.openrouter import OpenRouterLLM  # noqa: E402
from backend.kernel.schemas import SupervisorDecision  # noqa: E402

SUPERVISOR_TURN = (
    "OBJECTIVE: reconstruct the interval the fan missed, with evidence.\n"
    "STATE: fan's last rendered ball 16.2, resume position 16.6, 4 balls unaccounted.\n"
    "OBSERVATION:\n{\"boundary_candidates\": [\"evt_067\", \"evt_066\"], \"confidence\": 0.65, "
    "\"material_events_by_candidate\": {\"evt_066\": [\"evt_067\"], \"evt_067\": []}}\n"
    "SCRATCHPAD:\n{}\n\n"
    "AGENTS AVAILABLE:\ninvestigator - reads the clamped feed and the rendered frames\n"
    "strategist - picks the recovery modality\ncritic - checks a draft against the evidence\n"
    "TOOLS AVAILABLE:\nquery_feed - balls up to the fan's position\n"
    "read_frame - scoreboards from the pixels\n"
    "BUDGET REMAINING: {\"llm_calls\": 8, \"tool_calls\": 12, \"specialist_rounds\": 8}\n"
    "YOUR STEPS SO FAR: []\nChoose the single next step."
)

SPECIALIST_TURN = (
    "Cross-check the missed interval 16.3-16.6 for key actions and report only what the "
    "clamped feed and the rendered frames actually say."
)

DEFAULT_CANDIDATES = [
    "stealth/crouton-alpha",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "dots-studio/dots-3-note-preview:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-2.5-2.6b:free",
    "recursive/flint-v1.0:free",
]


def _verdict(text: str, schema) -> str:
    if not text.strip():
        return "no-content"
    try:
        obj = schema.model_validate_json(text.strip())
        return f"VALID {getattr(obj, 'action', getattr(obj, 'kind', ''))}".strip()
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            obj = schema.model_validate(json.loads(m.group(0)))
            return f"VALID(fenced) {getattr(obj, 'action', getattr(obj, 'kind', ''))}".strip()
        except Exception as exc:
            return f"INVALID {str(exc)[:70]}"
    return "INVALID no-json-object"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turn", choices=["supervisor", "specialist"], default="supervisor")
    ap.add_argument("--models", default="")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=900)
    args = ap.parse_args()

    llm = OpenRouterLLM(live=True, cassette_mode="replay")
    cands = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_CANDIDATES
    schema, user, system = (
        (SupervisorDecision, SUPERVISOR_TURN, SUP_CONTRACT) if args.turn == "supervisor"
        else (SpecialistTurn, SPECIALIST_TURN,
              "You are a bounded investigator. Reply with ONE JSON object."))

    print(f"turn={args.turn} reps={args.reps} endpoint={llm.base_url}")
    print(f"{'model':50s} {'code':>5s} {'s':>6s} {'out':>5s} verdict")
    for model in cands:
        for rep in range(args.reps):
            body = {"model": model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": 0.0, "max_tokens": args.max_tokens,
                    "response_format": {"type": "json_object"}}
            t0 = time.time()
            try:
                r = httpx.post(f"{llm.base_url}/chat/completions",
                               headers={"Authorization": f"Bearer {llm.api_key}",
                                        "Content-Type": "application/json"},
                               json=body, timeout=75)
                d = r.json() if r.content else {}
                ch = (d.get("choices") or [{}])[0]
                text = ((ch.get("message") or {}).get("content") or "")
                out = (d.get("usage") or {}).get("completion_tokens")
                note = (d.get("error") or {}).get("message", "")[:60]
                print(f"{model:50s} {r.status_code:>5d} {time.time() - t0:6.1f} "
                      f"{str(out):>5s} {_verdict(text, schema)} {note}")
            except Exception as exc:
                print(f"{model:50s} {'-':>5s} {time.time() - t0:6.1f} {'-':>5s} "
                      f"EXC {type(exc).__name__} {str(exc)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
