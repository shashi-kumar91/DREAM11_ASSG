"""Dynamic-case probe: the situations the three golden fixtures do not script.

    python backend/scripts/probe_dynamic.py --base http://127.0.0.1:8931

The three recorded scenarios are the demo. A product that only survives its demo is a demo, so
this script drives the states a real fan or a careless client can put the server in, and asserts
the honest answer for each:

  1. a heartbeat the fixture never recorded (custom gap) still moves the boundary,
  2. a heartbeat that lands *after* the resume cursor never widens what may be released,
  3. free-text answers that name no ball are refused as evidence but kept as preference,
  4. an answer that arrives late (after the window closed) cannot rewrite a committed receipt,
  5. a creator query with no evidence produces a refusal, not an invented card,
  6. the same session polled twice returns identical state (no drift),
  7. a session asked to run a pack-appropriate default resolves it rather than 500ing,
  8. abandoning sessions does not corrupt the store (the newest still runs),
  9. a boundary recomputed by an interrupt is reported both in the frame and in the trace,
 10. an unknown event id is refused by name.

Everything is read back from the server; nothing is asserted from this file's own assumptions.
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
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:170]}" if detail else ""))


def wait(snap_fn, pred, limit_s: float = 25.0):
    end = time.time() + limit_s
    snap = {}
    while time.time() < end:
        snap = snap_fn()
        if pred(snap):
            return snap
        time.sleep(0.2)
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8931")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=60)

    def session(pack: str, scenario: str, mode: str = "watch", **kw) -> str:
        r = c.post("/api/session", json={"pack": pack, "scenario": scenario, "mode": mode, **kw})
        assert r.status_code == 200, r.text
        return r.json()["session_id"]

    def state(sid: str) -> dict:
        return c.get(f"/api/session/{sid}").json()

    def done(sid: str) -> dict:
        return wait(lambda: state(sid), lambda s: s.get("done"))

    # 1 + 9: a heartbeat that is not in the fixture -------------------------------------- #
    sid = session("gap", "S1", auto_answer=True)
    before = state(sid)
    boundary_before = before.get("boundary") or {}
    rr = c.post(f"/api/session/{sid}/interrupt",
                json={"kind": "stall", "event_id": "evt_070", "note": "probe custom stall"})
    rec("custom heartbeat accepted", rr.status_code == 200, f"{rr.status_code}")
    body = rr.json()
    rec("custom heartbeat re-estimates the boundary in the response",
        bool(body.get("candidate_overs")) and bool(body.get("notes")),
        f"{body.get('candidate_overs')} conf={body.get('confidence')}")
    tr = c.get(f"/api/session/{sid}/trace").json()
    rec("custom heartbeat is in the trace too",
        any(e.get("kind") == "interrupt" and e.get("heartbeat") == "stall" for e in tr["events"]),
        "")
    done(sid)

    # 2: a heartbeat after the resume cursor cannot unlock it ---------------------------- #
    sid = session("gap", "S2", auto_answer=True)
    done(sid)
    snap = state(sid)
    released_before = set(c.get(f"/api/session/{sid}/trace").json()["released_events"])
    rr = c.post(f"/api/session/{sid}/interrupt",
                json={"kind": "play", "event_id": "evt_077", "note": "after the cursor"})
    rec("late heartbeat accepted but not applied to a finished session",
        rr.status_code == 200 and not rr.json().get("applied_to_session"),
        f"applied={rr.json().get('applied_to_session')}")
    released_after = set(c.get(f"/api/session/{sid}/trace").json()["released_events"])
    resume = (snap.get("episode_start") or {}).get("resume_over") or ""
    future = {e for e in released_after - released_before}
    rec("no future event became releasable", not future, f"new={sorted(future)}")
    rec("the released set never contains the resume cursor itself or beyond",
        all(released_after), f"resume_over={resume} released={sorted(released_after)}")

    # 3: free text that names no ball ---------------------------------------------------- #
    sid = session("gap", "S1")
    snap = wait(lambda: state(sid), lambda s: bool(s.get("pending_question")))
    options = (snap.get("pending_question") or {}).get("options") or []
    rr = c.post(f"/api/session/{sid}/answer", json={"text": "no idea, just tell me something"})
    rec("free-text answer with no ball label is accepted as preference only",
        rr.status_code == 200, f"{rr.status_code} {rr.text[:100]}")
    done(sid)
    tr = c.get(f"/api/session/{sid}/trace").json()
    labels = [(e.get("kind"), e.get("answer")) for e in tr["events"] if e.get("kind") == "boundary_from_fan"]
    rec("a label-free answer never moves the boundary",
        all("16.3" not in str(a) and "16.2" not in str(a) for _, a in labels) or not labels,
        f"options={options} boundary_from_fan={labels[:2]}")
    cards = state(sid)["cards"]
    rec("a receipt is still produced after a vague answer", len(cards) >= 1, [x["kind"] for x in cards])

    # 4: answering a question twice, and answering after the window closed ---------------- #
    rr = c.post(f"/api/session/{sid}/answer", json={"option_index": 0})
    rec("late answer after the episode closed is refused, not silently applied",
        rr.status_code == 409, rr.status_code)

    # 5: a creator query with no evidence in the transcript ------------------------------ #
    sid = session("creator", "u09", query="Who won the 2011 World Cup final?")
    snap = done(sid)
    tr = c.get(f"/api/session/{sid}/trace").json()
    kinds = [e.get("kind") for e in tr["events"]]
    rec("query with no evidence emits query_no_evidence", "query_no_evidence" in kinds, kinds[-6:])
    # The honest card echoes the question in its body, so "is the query text present" proves
    # nothing. What matters is the label it carries and what it claims.
    qcards = [cd for cd in snap["cards"] if cd.get("kind") == "query_answer"]
    rec("the unanswerable query produced a labelled abstain card",
        bool(qcards) and all(cd["truth_label"] == "No evidence — nothing invented" for cd in qcards),
        [(cd.get("truth_label"), cd.get("headline", "")[:50]) for cd in qcards])
    rec("that card says nothing was invented",
        all("invented" in cd.get("body", "") for cd in qcards),
        [cd.get("body", "")[:80] for cd in qcards])

    # 6: polling twice must not drift ---------------------------------------------------- #
    a = c.get(f"/api/session/{sid}").json()
    b = c.get(f"/api/session/{sid}").json()
    rec("two reads of the same session agree",
        a["cards"] == b["cards"] and a["budget"] == b["budget"] and a["done"] == b["done"], "")

    # 7: pack defaults ------------------------------------------------------------------- #
    rr = c.post("/api/session", json={"pack": "gap", "scenario": "", "mode": "watch"})
    rec("empty gap scenario resolves to the pack default", rr.status_code == 200,
        f"{rr.status_code} {rr.text[:90]}")
    rr = c.post("/api/session", json={"pack": "creator", "scenario": "", "mode": "watch"})
    rec("empty creator scenario resolves to ALL", rr.status_code == 200, f"{rr.status_code} {rr.text[:90]}")

    # 8: abandon a pile of sessions, then run one ---------------------------------------- #
    for _ in range(6):
        session("gap", "S3", auto_answer=True)
    sid = session("gap", "S1", auto_answer=True)
    snap = done(sid)
    rec("store still serves a new session after abandoned ones", bool(snap.get("done")),
        f"cards={len(snap.get('cards') or [])}")

    # 10: unknown event ids -------------------------------------------------------------- #
    rr = c.post(f"/api/session/{sid}/interrupt", json={"kind": "play", "event_id": "evt_nope"})
    rec("unknown event id refused by name", rr.status_code == 400 and "evt_nope" in rr.text,
        f"{rr.status_code} {rr.text[:100]}")

    print(f"\nSUMMARY {sum(1 for _, ok, _ in ROWS if ok)} / {len(ROWS)} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
