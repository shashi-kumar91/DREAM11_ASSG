"""StructuredClaim must survive what real models actually emit.

Every case here was observed from a live model turn during development, not invented:
an over written as "16.3", and the whole utterance pasted where the claim object belonged.
Losing either one costs the fan a card.
"""
import pytest

from backend.packs.creator.agents import AnalystOut
from backend.packs.creator.domain import Feed
from backend.packs.creator.verify import StructuredClaim, spoken_atoms, verify
from backend.kernel.schemas import VerdictKind


@pytest.mark.parametrize("raw,want", [
    ("16.3", 16),
    ("16", 16),
    (16.3, 16),
    (16, 16),
    ("", None),
    ("over 17.1", 17),
])
def test_over_accepts_label_shapes(raw, want):
    assert StructuredClaim(over=raw).over == want


def test_score_pair_accepts_strings():
    c = StructuredClaim(runs="142", wickets="4")
    assert (c.runs, c.wickets) == (142, 4)


def test_blank_kind_still_recovers_from_populated_fields():
    """The analyst sent kind:"" alongside runs/wickets. Absent-key inference alone missed it
    and the kernel reported 'no checkable claim' for a perfectly checkable score."""
    for blank in ("", None, "other"):
        c = StructuredClaim.model_validate({"kind": blank, "runs": 142, "wickets": 4})
        assert c.kind == "score", blank


def test_player_only_claim_recovers_as_wicket():
    c = StructuredClaim.model_validate({"kind": "", "player": "Vikram Naidu"})
    assert c.kind == "wicket_by_player"


def test_score_read_out_of_the_quote_when_fields_are_left_null():
    """The live analyst emitted exactly this: the right words quoted, no numbers extracted."""
    c = StructuredClaim.model_validate({"kind": "other",
                                        "raw_phrase": "Score 142 hai abhi, 142/3, same as before."})
    assert (c.kind, c.runs, c.wickets) == ("score", 142, 3)


def test_quote_extraction_still_has_to_be_grounded():
    feed = Feed.load()
    c = StructuredClaim(raw_phrase="he said 999/9")
    v = verify(c, "Score 142/3 hai.", feed, feed.state_at(133.7))
    assert v.kind is VerdictKind.UNVERIFIABLE


def test_conflict_card_is_producible_for_the_stale_score():
    """u07: creator says 142/3 after the feed moved to 147/5 - the demo's jury moment."""
    feed = Feed.load()
    c = StructuredClaim.model_validate({"kind": "", "raw_phrase": "Score abhi 142/3 hai"})
    v = verify(c, "Score 142 hai abhi, 142/3, same as before.", feed, feed.state_at(133.7))
    assert v.kind is VerdictKind.CONFLICT
    assert v.observed == "147/5"
    assert v.matched_over == "16.2" and v.age_balls == 4


def test_genuinely_empty_claim_stays_unusable():
    assert StructuredClaim.model_validate({"kind": ""}).kind == "other"


def test_unknown_kind_degrades_instead_of_failing():
    c = StructuredClaim(kind="score_of_match")
    assert c.kind == "other"


def test_rate_parses_from_prose():
    assert StructuredClaim(rate="9.67 runs per over").rate == pytest.approx(9.67)


def test_bare_string_claim_keeps_its_phrase():
    """Gemini emitted: claim = "Vikram Naidu out ho gaye. Score abhi 142/4 hai." """
    out = AnalystOut.model_validate({"insight_type": "factual_claim",
                                     "claim": "Vikram Naidu out ho gaye. Score abhi 142/4 hai."})
    assert out.claim.raw_phrase.startswith("Vikram Naidu out ho gaye")


def test_bare_string_prediction_keeps_its_text():
    out = AnalystOut.model_validate({"insight_type": "prediction",
                                     "prediction": "Bedi agle over mein do yorker phenkega"})
    assert out.prediction.text.startswith("Bedi agle over")


def test_object_shape_still_parses():
    c = StructuredClaim.model_validate({"kind": "score", "raw_phrase": "142/4",
                                        "runs": 142, "wickets": 4, "over": "16.3"})
    v = verify(c, "Score abhi 142/4 hai.", Feed.load(), Feed.load().state_at(99.1))
    assert v.kind in (VerdictKind.CONFIRMED, VerdictKind.CONFIRMED_LAG, VerdictKind.CONFLICT)


def test_spoken_atoms_decompose_but_do_not_substring_match():
    """"142/4" vouches for 142 and 4. It must not vouch for 42 or 14."""
    atoms = spoken_atoms("Score abhi 142/4 hai, rrr 9.67")
    assert {"142", "4", "142/4", "9.67"} <= atoms
    assert "42" not in atoms and "14" not in atoms


def test_invented_wicket_count_is_refused_not_verified():
    """The analyst claims 142/7 when the creator only ever said 142/4."""
    feed = Feed.load()
    c = StructuredClaim(kind="score", raw_phrase="142/7", runs=142, wickets=7)
    v = verify(c, "Score abhi 142/4 hai.", feed, feed.state_at(99.1))
    assert v.kind is VerdictKind.UNVERIFIABLE
    assert "extraction_not_grounded" in v.detail
