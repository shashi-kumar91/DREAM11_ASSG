"""Drive one scenario over the real HTTP surface and report what the fan actually received.

Why this exists: the runner scripts call the session in-process, so they cannot see what a
browser sees - a second ask nobody answers, a card built but never emitted, an SSE frame lost on
the way out. This is the script that proves the link a jury opens works, and it fails loudly when
the fan would be left with silence.

    python -u backend/scripts/drive_http.py --base http://127.0.0.1:8127 --pack gap --scenario S1
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _frames(client: httpx.Client, base: str, sid: str, deadline: float, log):
    """Yield (event, payload) from the session journal, replay-then-follow, until `done`."""
    with client.stream("GET", f"{base}/api/session/{sid}/events",
                      timeout=httpx.Timeout(30.0, read=20.0)) as r:
        r.raise_for_status()
        event, buf = "", ""
        for line in r.iter_lines():
            if not line:
                if event and buf:
                    payload = json.loads(buf)
                    if log:
                        log.write(json.dumps({"event": event, "payload": payload}) + "\n")
                        log.flush()
                    yield event, payload
                    if event == "done":
                        return
                event, buf = "", ""
                continue
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                buf = line.split(":", 1)[1].strip()
            if time.time() > deadline:
                yield "timeout", {"after_s": deadline - time.time()}
                return


def drive(base: str, pack: str, scenario: str, answer: str, mode: str, log) -> dict:
    seen: list[tuple[str, dict]] = []
    asked: list[dict] = []
    cards: list[dict] = []
    with httpx.Client() as client:
        r = client.post(f"{base}/api/session", json={"pack": pack, "scenario": scenario,
                                                     "mode": mode})
        r.raise_for_status()
        sid = r.json()["session_id"]
        print(f"session {sid}  pack={pack} scenario={scenario} mode={mode}")
        deadline = time.time() + 300
        for event, payload in _frames(client, base, sid, deadline, log):
            seen.append((event, payload))
            if event == "ask_user":
                q = payload.get("question") or {}
                asked.append(q)
                text = answer or (q.get("options") or ["Yes"])[0]
                a = client.post(f"{base}/api/session/{sid}/answer", json={"text": text})
                print(f"  asked: {q.get('text','')[:70]}\n  answered: {text} -> {a.status_code}")
            elif event == "card":
                cards.append(payload["card"])
                print(f"  card [{payload['card']['truth_label']}] "
                      f"{payload['card']['headline'][:70]}")
            elif event == "status":
                print(f"  status: {payload.get('note','')[:100]}")
            elif event == "error":
                print(f"  ERROR {payload}")
            elif event == "done":
                got = client.get(f"{base}/api/session/{sid}/trace").json()
                return {"session_id": sid, "done": payload, "events": seen, "asked": asked,
                        "cards": cards, "trace_kinds": _kinds(got.get("events", []))}
        return {"session_id": sid, "done": None, "events": seen, "asked": asked,
                "cards": cards, "trace_kinds": {}}


def _kinds(events: list[dict]) -> dict:
    out: dict[str, int] = {}
    for e in events:
        out[e.get("kind", "?")] = out.get(e.get("kind", "?"), 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    ap.add_argument("--pack", default="gap")
    ap.add_argument("--scenario", default="S1")
    ap.add_argument("--answer", default="")
    ap.add_argument("--mode", default="watch", choices=["watch", "quiet"])
    ap.add_argument("--log", default="")
    a = ap.parse_args()
    health = httpx.get(f"{a.base}/api/health", timeout=10).json()
    print(f"server {a.base} run_mode={health.get('run_mode')} "
          f"backend={health.get('llm_backend')} cassettes={health.get('cassettes')}")
    t0 = time.time()
    with contextlib.nullcontext() if not a.log else open(a.log, "w", encoding="utf-8") as log:
        res = drive(a.base, a.pack, a.scenario, a.answer, a.mode, log)
    kinds = res["trace_kinds"]
    print(f"\n{a.scenario}: {len(res['cards'])} card(s), {len(res['asked'])} ask(s), "
          f"{len(res['events'])} SSE frame(s), {round(time.time()-t0,1)}s")
    print(f"done frame: {json.dumps(res['done'], default=str)[:400] if res['done'] else 'MISSING'}")
    print(f"trace kinds: {json.dumps(kinds, sort_keys=True)}")
    fails = []
    if not res["cards"]:
        fails.append("the fan received no card")
    if res["done"] is None:
        fails.append("the stream never ended with `done`")
    if len(res["asked"]) > 1:
        fails.append(f"the fan was asked {len(res['asked'])} times over HTTP")
    for f in fails:
        print(f"FAIL: {f}")
    print("PASS" if not fails else f"{len(fails)} failure(s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
