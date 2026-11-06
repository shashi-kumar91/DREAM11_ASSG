"""Tool plumbing: a validated args model must reach the tool as a model, not a dict.

Regression: the registry used to call `.model_dump()` after validation, so every tool that
read `args.start_over` raised AttributeError and the agents saw a wall of tool_error.
"""
from pydantic import BaseModel

from backend.kernel.registry import Tool, ToolRegistry
from backend.packs.creator.agents import AnalystOut


class Args(BaseModel):
    start_over: str = ""
    only_material: bool = False


def test_tool_receives_validated_model():
    seen = {}

    def fn(a: Args) -> dict:
        seen["type"] = type(a).__name__
        return {"got": a.start_over, "mat": a.only_material}

    reg = ToolRegistry()
    reg.register(Tool("probe", "test", fn, ("match_state",), Args))
    res = reg.call("probe", {"start_over": "16.0"})
    assert res.ok, res.error
    assert seen["type"] == "Args"
    assert res.data == {"got": "16.0", "mat": False}


def test_missing_optional_args_still_validate():
    def fn(a: Args) -> dict:
        return {"got": a.start_over}

    reg = ToolRegistry()
    reg.register(Tool("probe", "test", fn, (), Args))
    assert reg.call("probe", {}).ok


def test_numeric_over_is_coerced_not_rejected():
    """The real risk: an agent says start_over=16 and the tool schema says str."""
    from backend.packs.creator.tools import FeedRangeArgs

    reg = ToolRegistry()
    reg.register(Tool("probe", "test", lambda a: {"got": a.start_over}, (), FeedRangeArgs))
    res = reg.call("probe", {"start_over": 16, "bogus": 1})
    assert res.ok, res.error
    assert res.data == {"got": "16"}


def test_bad_args_return_structured_error():
    reg = ToolRegistry()
    reg.register(Tool("probe", "test", lambda a: {}, (), AnalystOut))
    res = reg.call("probe", {"salience": 5.0})
    assert not res.ok and res.error.startswith("bad_args:")


def test_analyst_tolerates_explicit_nulls_from_gemini():
    """Gemini emits `claim: null` for a prediction utterance; that is not a schema failure
    worth burning a retry on - it means "no claim"."""
    out = AnalystOut.model_validate({"insight_type": "prediction", "claim": None,
                                    "prediction": None})
    assert out.claim.kind == "other"
    assert out.prediction.text == ""
