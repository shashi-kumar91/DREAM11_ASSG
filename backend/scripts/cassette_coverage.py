"""Which product paths does the recorded store actually serve?

`tests/cassettes` is the reason the demo runs with no network and no key. A recording is keyed
by the exact prompt, so any change to a prompt silently orphans the turns around it and the
path degrades to the deterministic card. That is honest behaviour, but it must not be a
surprise on demo day - so this script drives every path and says which model turns had to fall
back.

    python backend/scripts/cassette_coverage.py                 # needs the server on :8931
    python backend/scripts/cassette_coverage.py --json out.json

It talks to the running server over HTTP, so it measures the product, not a copy of its logic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BASE = os.getenv("PROBE_BASE") or "http://127.0.0.1:8931"
GAP = (("S1", "the ad gap that asks"), ("S2", "the stall with no key action"),
       ("S3", "the late join with the widest window"))
CREATOR = ("ALL", "the twelve-segment stream")
ANSWER_SLOTS = (0, 1)


def _post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _get(path: str, timeout: float = 30.0) -> dict:
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


def _wait(sid: str, budget_s: float = 240.0) -> dict:
    end = time.time() + budget_s
    while time.time() < end:
        st = _get(f"/api/session/{sid}")
        if st.get("done"):
            return st
        time.sleep(0.5)
    return _get(f"/api/session/{sid}")


def _frames(sid: str, settle_s: float = 1.0) -> list[dict]:
    """Read the session's SSE stream once the run is over: it replays the whole journal."""
    out: list[dict] = []
    try:
        with urllib.request.urlopen(f"{BASE}/api/session/{sid}/events", timeout=settle_s + 20) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    out.append(json.loads(line[5:].strip()))
                except Exception:
                    continue
                if out and out[-1].get("type") == "done":
                    break
    except Exception:
        pass
    return out


def _kinds(frames: list[dict], kind: str) -> int:
    return sum(1 for f in frames if f.get("type") == kind)


def run_case(pack: str, scenario: str, answer: int | None = None,
             extra: dict | None = None) -> dict:
    payload = {"pack": pack, "scenario": scenario, "mode": "watch", **(extra or {})}
    st = _post("/api/session", payload)
    sid = st["session_id"]
    if answer is not None:
        for _ in range(40):
            cur = _get(f"/api/session/{sid}")
            if cur.get("pending_question") or cur.get("done"):
                break
            time.sleep(0.4)
        opts = ((cur.get("pending_question") or {}).get("options") or [])
        if opts:
            _post(f"/api/session/{sid}/answer",
                  {"option_index": min(answer, len(opts) - 1)})
    done = _wait(sid)
    frames = _frames(sid)
    cards = done.get("cards", [])
    return {
        "pack": pack, "scenario": scenario, "answered": answer,
        "layer": done.get("llm_layer"),
        "note": (done.get("llm_note") or "")[:120],
        "cards": [(c["kind"], c["headline"][:52]) for c in cards],
        "model_turns": _kinds(frames, "supervisor_decision"),
        "model_errors": _kinds(frames, "supervisor_error"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--no-answers", action="store_true",
                    help="also run the paths where the fan never answers")
    a = ap.parse_args()

    rows: list[dict] = []
    for sid, _ in GAP:
        for slot in ANSWER_SLOTS:
            rows.append(run_case("gap", sid, slot))
    rows.append(run_case("creator", CREATOR[0]))
    if not a.no_answers:
        for sid, _ in GAP:
            rows.append(run_case("gap", sid, None))

    blind = [r for r in rows if r["model_errors"]]
    for r in rows:
        flag = "FALLBACK" if r["model_errors"] else "ok"
        print(f"{flag:9} {r['pack']}/{r['scenario']} ans={r['answered']} "
              f"turns={r['model_turns']} errors={r['model_errors']} cards={len(r['cards'])}"
              + (f" | {r['note']}" if r["note"] else ""))
    print(f"\n{len(rows) - len(blind)}/{len(rows)} paths fully served from the recorded store")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))
    return 0 if not blind else 1


if __name__ == "__main__":
    sys.exit(main())
