"""The facts endpoint is the UI's only source of numbers, so it gets its own acceptance tests.

The bug these guard is not a crash: it was a page that rendered confidently wrong figures
("175/8" for the chasing side, a "+0.40 (9.27 -> 9.67)" strip painted onto a receipt whose real
shift was 8.48 -> 11.57, a fault frame described as "agrees"). A payload that is wrong is worse
than a payload that is missing, so the numbers are asserted against the fixture files directly.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.server import app
from backend.packs.gap.domain import GapFixtures


@pytest.fixture(scope="module")
def facts() -> dict:
    with TestClient(app) as client:
        r = client.get("/api/facts")
        assert r.status_code == 200
        return r.json()


def test_facts_reports_the_feed_state_not_a_typed_number(facts: dict) -> None:
    from backend.packs.creator.domain import FIXTURES

    feed = json.loads((FIXTURES / "match_feed.json").read_text(encoding="utf-8"))
    last = feed[-1]
    assert facts["state"]["score"] == last["score"]
    assert facts["state"]["last_over"] == last["over_label"]
    assert facts["state"]["balls_in_fixture"] == len(feed)
    assert facts["state"]["wickets"] == sum(1 for e in feed if e["wicket"])
    # The first UI build said 175/8 was the chasing side's score; the chase can never lead the
    # target in these fixtures, and the payload must not invent a second score at all.
    assert "175/8" not in json.dumps(facts["state"])


def test_every_feed_row_carries_what_the_strip_renders(facts: dict) -> None:
    assert len(facts["feed"]) == facts["state"]["balls_in_fixture"]
    for row in facts["feed"]:
        assert row["over"] and row["score"] and isinstance(row["runs"], int)
        assert isinstance(row["wicket"], bool)
    overs = [r["over"] for r in facts["feed"]]
    assert overs == sorted(overs, key=lambda o: tuple(int(x) for x in o.split(".")))


def test_the_injected_fault_frame_is_reported_as_disagreeing(facts: dict) -> None:
    faults = [f for f in facts["frames"] if f["injected_fault"]]
    assert len(faults) == 1, "the fixture set is supposed to ship exactly one injected fault"
    fault = faults[0]
    assert fault["ocr"]["readable"] is True
    assert fault["ocr"]["score"] == fault["drawn_score"]
    assert fault["feed_score"] != fault["drawn_score"], (
        "the UI decides 'agrees vs refused' by comparing the pixel read with the FEED score, so "
        "that comparison has to be able to fail")


def test_agreeing_frames_read_exactly_what_the_feed_says(facts: dict) -> None:
    for f in facts["frames"]:
        if f["injected_fault"]:
            continue
        assert f["ocr"]["readable"] is True
        assert f["ocr"]["score"] == f["feed_score"] == f["drawn_score"]
        assert f["ocr"]["over"] == f["over"]


def test_frame_images_are_served_for_every_frame(facts: dict) -> None:
    with TestClient(app) as client:
        for f in facts["frames"]:
            r = client.get(f["url"])
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert len(r.content) > 1000
        assert client.get("/api/media/frame/nope").status_code == 404


def test_scenario_cards_match_the_scenarios_the_agent_runs(facts: dict) -> None:
    gap = GapFixtures.load()
    assert [s["id"] for s in facts["gap_scenarios"]] == list(gap.scenarios)
    for row in facts["gap_scenarios"]:
        scenario = gap.scenarios[row["id"]]
        resume = gap.feed.by_id[scenario.resume_cursor]
        assert row["resume_over"] == resume.over_label
        assert row["resume_score"] == resume.score
        assert row["asks"] == bool(scenario.expected.get("asks"))


def test_provenance_is_served_and_names_the_simulated_parts(facts: dict) -> None:
    surfaces = " ".join(p["surface"] for p in facts["provenance"]).lower()
    for expected in ("match", "telemetry", "audio", "clip", "integration"):
        assert expected in surfaces
    states = {p["surface"]: p["state"] for p in facts["provenance"]}
    assert "simulated" in states["The match on screen"]
    assert states["Dream11 / FanCode integration"] == "absent"


def test_photo_credits_ship_with_a_licence_and_a_source(facts: dict) -> None:
    credits = facts["photo_credits"]
    assert credits, "the UI renders photographs; every one of them must carry its licence"
    for c in credits:
        assert c["license"] and c["source"].startswith("https://")
        assert c["artist"]
