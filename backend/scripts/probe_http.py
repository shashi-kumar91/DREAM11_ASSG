"""Black-box contract probe for a running server.

    python backend/scripts/probe_http.py [--base http://127.0.0.1:8931]

Why this exists next to `pytest tests`: the tests import the app and drive it in process, which
cannot catch a route that is mounted differently, an SSE header a proxy strips, or a refusal
that only appears over the wire. This script talks to a real server and prints PASS/FAIL per
case, exiting non-zero if anything fails, so it can gate a deploy.

It creates and abandons real sessions (they are in-process and capped by MAX_SESSIONS), and it
never asserts on a value it did not read back from the server.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

ROWS: list[tuple[str, bool, str]] = []
FAILED = 0


def rec(name: str, ok: bool, detail: object = "") -> None:
    global FAILED
    ROWS.append((name, ok, str(detail)))
    if not ok:
        FAILED += 1
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:160]}" if detail else ""))


def wait_done(c: httpx.Client, sid: str, limit_s: float = 30.0) -> dict:
    end = time.time() + limit_s
    snap: dict = {}
    while time.time() < end:
        snap = c.get(f"/api/session/{sid}").json()
        if snap.get("done"):
            return snap
        time.sleep(0.25)
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8931")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=60)

    # -- request validation -------------------------------------------------- #
    r = c.post("/api/session", json={"pack": "nope", "scenario": "S1", "mode": "watch"})
    rec("unknown pack rejected", r.status_code == 400, f"{r.status_code} {r.text[:100]}")
    r = c.post("/api/session", json={"pack": "gap", "scenario": "S9", "mode": "watch"})
    rec("unknown gap scenario rejected", r.status_code == 400, f"{r.status_code} {r.text[:100]}")
    r = c.post("/api/session", json={"pack": "creator", "scenario": "u999", "mode": "watch"})
    rec("unknown creator segment rejected", r.status_code == 400, f"{r.status_code} {r.text[:100]}")
    r = c.post("/api/session", json={"pack": "gap", "scenario": "S1", "mode": "loud"})
    rec("bad player mode rejected", r.status_code == 400, f"{r.status_code} {r.text[:100]}")
    r = c.post("/api/session", content=b"{not json", headers={"content-type": "application/json"})
    rec("malformed JSON rejected cleanly", r.status_code == 422, r.status_code)

    # -- sessions ------------------------------------------------------------ #
    r = c.post("/api/session", json={"pack": "gap", "scenario": "S1", "mode": "watch"})
    sid = r.json()["session_id"]
    rec("session created", r.status_code == 200, sid)
    for path in ("/api/session/nope", "/api/session/nope/trace", "/api/session/nope/bytes"):
        rr = c.get(path)
        rec(f"404 {path}", rr.status_code == 404, rr.status_code)

    # A pack that never asks: posting an answer there has nothing to resolve.
    noask = c.post("/api/session",
                   json={"pack": "creator", "scenario": "u09", "mode": "watch"}).json()["session_id"]
    rr = c.post(f"/api/session/{noask}/answer", json={"text": "yes"})
    rec("answer with nothing pending -> 409", rr.status_code == 409, f"{rr.status_code} {rr.text[:110]}")

    # -- interrupts ---------------------------------------------------------- #
    rr = c.post(f"/api/session/{sid}/interrupt", json={"kind": "explode"})
    rec("unknown interrupt kind rejected", rr.status_code == 400, f"{rr.status_code} {rr.text[:110]}")
    rr = c.post(f"/api/session/{sid}/interrupt", json={"kind": "play", "event_id": "evt_999"})
    rec("interrupt with unknown event refused", rr.status_code == 400,
        f"{rr.status_code} {rr.text[:110]}")

    # -- the boundary question ----------------------------------------------- #
    asked = ""
    for _ in range(40):
        snap = c.get(f"/api/session/{sid}").json()
        asked = (snap.get("pending_question") or {}).get("text", "")
        if asked:
            break
        time.sleep(0.15)
    rec("boundary question was asked", bool(asked), asked)
    if asked:
        rr = c.post(f"/api/session/{sid}/answer", json={"option_index": 1})
        rec("answer accepted", rr.status_code == 200, rr.text[:80])
        rr = c.post(f"/api/session/{sid}/answer", json={"option_index": 1})
        rec("second answer refused -> 409", rr.status_code == 409, f"{rr.status_code} {rr.text[:110]}")
        rr = c.post(f"/api/session/{sid}/answer", json={"option_index": 9})
        rec("out-of-range option refused -> 409/400", rr.status_code in (400, 409), rr.status_code)

    # -- the run ------------------------------------------------------------- #
    snap = wait_done(c, sid)
    tr = c.get(f"/api/session/{sid}/trace").json()
    rec("run finished", bool(snap.get("done")),
        f"cards={len(snap.get('cards', []))} llm={snap.get('budget', {}).get('llm_calls')}")
    rec("at least one card committed", len(snap.get("cards", [])) >= 1, len(snap.get("cards", [])))
    released = set(tr.get("released_events") or [])
    rec("released events are non-empty (something was released on purpose)",
        bool(released), sorted(released)[:6])
    rec("no card cites an event outside the released set",
        all(set(cd.get("cited_event_ids") or []) <= released for cd in snap.get("cards", [])), "")
    rec("badges present", set(snap.get("badges", {})) >= {"llm", "feed", "telemetry"},
        snap.get("badges"))
    rec("done frame carries a byte snapshot", "bytes" in snap and snap["bytes"] is not None, "")

    # -- spoiler walls on the media route ------------------------------------ #
    quiet = c.post("/api/session", json={"pack": "gap", "scenario": "S2", "mode": "quiet"}).json()
    wait_done(c, quiet["session_id"])
    rr = c.get(f"/api/media/clip/evt_072?sid={quiet['session_id']}")
    rec("quiet mode refuses clip bytes (403)", rr.status_code == 403, f"{rr.status_code}")
    rr = c.get(f"/api/media/clip/evt_077?sid={quiet['session_id']}")
    rec("clip beyond the resume cursor refused", rr.status_code in (403, 404), rr.status_code)
    rr = c.get(f"/api/media/clip/evt_999?sid={quiet['session_id']}")
    rec("clip for an unknown event refused", rr.status_code in (403, 404), rr.status_code)
    rr = c.get("/api/media/frame/frm_16_6")
    rec("frame bytes served", rr.status_code == 200 and rr.headers.get("content-type", "").startswith("image/"),
        f"{rr.status_code} {rr.headers.get('content-type')}")

    # -- facts and roster ---------------------------------------------------- #
    facts = c.get("/api/facts").json()
    rec("/api/facts has feed, frames, scenarios",
        bool(facts.get("feed")) and bool(facts.get("frames")) and bool(facts.get("gap_scenarios")),
        f"{len(facts.get('feed', []))} balls, {len(facts.get('frames', []))} frames")
    rec("/api/facts marks the fixture fictional", facts.get("match", {}).get("fictional") is True,
        facts.get("match", {}).get("fictional"))
    km = c.get("/api/kernel").json()
    rec("/api/kernel lists both packs", set(km.get("packs", {})) == {"gap", "creator"},
        sorted(km.get("packs", {})))
    rec("/api/kernel roster matches the kernel",
        [a["role"] for a in km["packs"]["gap"]["agents"]]
        == ["reader", "investigator", "questioner", "strategist", "critic"], "")

    # -- SSE resume ---------------------------------------------------------- #
    sid2 = c.post("/api/session", json={"pack": "gap", "scenario": "S2", "mode": "watch"}).json()["session_id"]
    wait_done(c, sid2)
    with c.stream("GET", f"/api/session/{sid2}/events", headers={"Last-Event-ID": "3"}) as s:
        first = None
        for line in s.iter_lines():
            if line.startswith("id: "):
                first = line[4:]
                break
    rec("SSE honours Last-Event-ID", first is not None and int(first) > 3, f"first id = {first}")

    # -- concurrency --------------------------------------------------------- #
    sids = [c.post("/api/session", json={"pack": "gap", "scenario": "S2", "mode": "watch"}).json()
            for _ in range(4)]
    rec("parallel sessions accepted", all(x.get("session_id") for x in sids), len(sids))
    oks = [bool(wait_done(c, x["session_id"])["done"]) for x in sids]
    rec("parallel sessions all finish", all(oks), oks)

    print(f"\nSUMMARY {sum(1 for _, ok, _ in ROWS if ok)} / {len(ROWS)} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
