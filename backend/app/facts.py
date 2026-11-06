"""Everything the screen is allowed to state as fact, in one payload.

Why this module exists at all. The first version of the UI hard-coded scoreboard values,
receipt metrics and "what the agent did" copy into JSX. Several of those numbers were simply
wrong - the banner said 175/8 for the chasing side, the receipt strip said "+0.40 (9.27 ->
9.67)" for every receipt including the one whose real shift was 8.48 -> 11.57, and one callout
described a stall as "2 dot balls" when the feed has 17.1 and 17.2 as dots but the scenario's
resume cursor sits at 17.3. Presentation is allowed to be pretty; it is not allowed to author
a number.

So: every figure the interface renders comes from here, and here it comes from the same
fixture files the agent runs on. `GET /api/facts` is read-only and touches no session.

The two derived fields worth knowing about:

* `frames[].ocr` is a live re-read of the rendered scoreboard bitmap by
  `backend/packs/gap/ocr.py` - the same pixel reader the receipt path uses. It is a
  diagnostic, not part of a run, and it exists so a reader can see that the number on screen
  came out of pixels rather than out of `frames.json` (the injected-fault frame reads 143/4,
  which the feed never held).
* `provenance` is the honest real-vs-simulated table. It is served, not typed into the
  frontend, so the page cannot drift away from the repo.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.packs.creator.domain import FIXTURES, Feed
from backend.packs.gap import ocr as frame_reader
from backend.packs.gap.domain import GapFixtures


def _read_json(name: str) -> Any:
    import json

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _feed_rows(feed: Feed) -> list[dict]:
    rows: list[dict] = []
    for e in feed.events:
        rows.append({
            "id": e.id,
            "over": e.over_label,
            "t": e.t_wall,
            "kind": e.kind,
            "runs": e.runs,
            "wicket": bool(e.wicket),
            "score": e.score,
            "rrr": e.rrr,
            "crr": e.crr,
            "text": e.text,
            "bowler": e.bowler,
            "striker": e.striker,
            "out": e.out,
        })
    return rows


def _frames(gap: GapFixtures) -> list[dict]:
    out: list[dict] = []
    for f in gap.frames:
        try:
            read = frame_reader.read_frame(f.image_bytes())
        except Exception as exc:                       # a missing font must not 500 the page
            read = {"score": None, "over": None, "rrr": None, "readable": False,
                    "error": f"{type(exc).__name__}: {exc}"}
        # `true_score` in frames.json is what the *picture* was drawn with - for the injected
        # fault that is deliberately not the feed's score. The feed's own value is resolved here
        # so the UI compares picture-vs-feed instead of picture-vs-itself (which is how an early
        # build ended up calling the fault frame "agrees").
        feed_ev = next((e for e in gap.feed.events if e.over_label == f.over_label), None)
        out.append({
            "frame_id": f.frame_id,
            "over": f.over_label,
            "drawn_score": f.true_score,
            "feed_score": feed_ev.score if feed_ev else None,
            "feed_rrr": feed_ev.rrr if feed_ev else None,
            "true_score": f.true_score,
            "true_rrr": f.true_rrr,
            "injected_fault": f.injected_fault,
            "url": f"/api/media/frame/{f.frame_id}",
            "ocr": read,
        })
    return out


def _gap_scenarios(gap: GapFixtures) -> list[dict]:
    rows: list[dict] = []
    for s in gap.scenarios.values():
        resume = gap.feed.by_id.get(s.resume_cursor)
        rows.append({
            "id": s.id,
            "name": s.name,
            "blurb": s.blurb,
            "interruption_type": s.interruption_type,
            "resume_over": resume.over_label if resume else "",
            "resume_score": resume.score if resume else "",
            "asks": bool(s.expected.get("asks")),
            "expected": s.expected,
            "heartbeats": [h.model_dump() for h in s.heartbeats],
        })
    return rows


def _creator(transcript: list[dict]) -> dict:
    return {
        "segments": [{
            "id": r["id"],
            "t_start": r["t_start"],
            "t_end": r["t_end"],
            "text": r["text"],
            "speaker": r["speaker"],
            "noisy": r.get("noisy", False),
            "asr_confidence_fixture": r.get("asr_confidence_fixture"),
            "audio": f"/audio/{r['id']}.wav",
        } for r in transcript]
    }


def _provenance(run_mode: str, llm_backend: str, cassettes: int) -> list[dict]:
    """The real-vs-simulated table, said in words a non-engineer can check."""
    return [
        {"surface": "The match on screen",
         "state": "simulated",
         "detail": "18 balls of a fictional T20 chase (Chennai Kestrels v Mumbai Vipers). "
                   "No real Dream11, FanCode or IPL data is used anywhere."},
        {"surface": "Fan's phone telemetry",
         "state": "simulated",
         "detail": "The heartbeat log that says when the player was watching, ad-blocked or "
                   "stalled is authored in the fixture file, not captured from a device."},
        {"surface": "Scoreboard photos on screen",
         "state": "real photos, licensed",
         "detail": "Licensed Wikimedia Commons photographs set the scene. Each one is "
                   "credited at the bottom of this page; none of them is evidence about "
                   "this match."},
        {"surface": "Reading a scoreboard frame",
         "state": "real pixel reading",
         "detail": "The five rendered scoreboard frames are read glyph by glyph with numpy "
                   "and Pillow - the reader reports what the image shows, including the one "
                   "frame that was deliberately drawn wrong (143/4)."},
        {"surface": "Agent reasoning",
         "state": _llm_state(run_mode, llm_backend, cassettes),
         "detail": _llm_detail(run_mode, llm_backend, cassettes)},
        {"surface": "Creator's Hinglish audio",
         "state": "text-to-speech",
         "detail": "The 12 creator segments are a TTS voice reading a written script, with a "
                   "synthetic crowd bed. Editing, not a real streamer."},
        {"surface": "Clip download",
         "state": "priced stand-in",
         "detail": "There is no encoded video. The clip endpoint streams a byte-shaped stand-in "
                   "of the exact size the policy would have charged, so the data meter is "
                   "honest about the policy and says nothing about codec efficiency."},
        {"surface": "What happens when no model turn is available",
         "state": "stated on screen",
         "detail": "A replayed turn only exists for a state that was recorded. When a run reaches "
                   "a state the store does not hold, the episode says so in one line and the "
                   "kernel finishes the card in code - the receipt is arithmetic over cited "
                   "balls, so it is complete without the model. Nothing is invented to hide it."},
        {"surface": "A situation nobody scripted",
         "state": "answered by the model pool",
         "detail": "Type any creator line, or name any ball and interruption, and the request "
                   "runs on the live model pool with no recording to fall back on; the response "
                   "names the layer it used."},
        {"surface": "Dream11 / FanCode integration",
         "state": "absent",
         "detail": "No login, no payments, no ad stack, no real creator feed. This is a "
                   "prototype of the loop, not a product integration."},
    ]


def _llm_state(run_mode: str, backend: str, cassettes: int) -> str:
    if run_mode in ("live-demo", "safe-demo"):
        return "live model calls"
    return f"recorded model turns ({cassettes} on disk)"


def _llm_detail(run_mode: str, backend: str, cassettes: int) -> str:
    if run_mode in ("live-demo", "safe-demo"):
        return (f"Run mode {run_mode}: turns the recorded store does not already hold are sent "
                f"to {backend}. Every card is still labelled by code, never by the model.")
    return (f"Run mode {run_mode}: the {cassettes} recorded model turns are replayed by hash, so "
            "the page works with no key and no network. With the model switched off entirely "
            "the kernel still commits its deterministic cards - that is the no-silence test.")


def build_facts(*, run_mode: str, llm_backend: str, cassettes: int,
                gap: Optional[GapFixtures] = None, feed: Optional[Feed] = None,
                transcript: Optional[list[dict]] = None, manifest: Optional[dict] = None) -> dict:
    from backend.packs.gap.domain import GapFixtures as _GF

    gap = gap or _GF.load()
    meta = _read_json("match_meta.json")
    transcript = transcript if transcript is not None else _read_json("transcript_expected.json")
    feed_rows = _feed_rows(gap.feed)
    last = feed_rows[-1] if feed_rows else {}
    return {
        "match": meta,
        # The chase as the feed stands at its last ball; the UI never types a score itself.
        "state": {
            "last_over": last.get("over", ""),
            "score": last.get("score", ""),
            "rrr": last.get("rrr"),
            "balls_in_fixture": len(feed_rows),
            "wickets": sum(1 for r in feed_rows if r["wicket"]),
            "boundaries": sum(1 for r in feed_rows if r["runs"] >= 4),
            "window": f"{feed_rows[0]['over']}-{feed_rows[-1]['over']}" if feed_rows else "",
        },
        "feed": feed_rows,
        "frames": _frames(gap),
        "gap_scenarios": _gap_scenarios(gap),
        "creator": _creator(transcript),
        "provenance": _provenance(run_mode, llm_backend, cassettes),
        "run": {"run_mode": run_mode, "llm_backend": llm_backend, "cassettes": cassettes},
        "photo_credits": _photo_credits(),
    }


def _photo_credits() -> list[dict]:
    path = FIXTURES.parents[1] / "frontend" / "public" / "img" / "credits.json"
    if not path.is_file():
        return []
    import json

    rows = json.loads(path.read_text(encoding="utf-8"))
    for r in rows:
        # The build flattens `public/` to the site root, so the provenance is the served path.
        r["image"] = r.get("file", "")
    return rows
