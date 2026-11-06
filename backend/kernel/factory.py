"""Build the text backend from configuration, not from a hardcoded class.

Three layers, and the caller can ask for any of them by name:

  * `replay`  - recorded turns only (cassette). Instant, offline, spends nothing.
  * `live`    - a pool of real models (`MultiLLM`), rotated per call, with per-model cooldown.
  * `off`     - no transport at all; the kernel's deterministic fallback still ships a card.

RUN_MODE picks the default (`cached-demo` -> replay, `live-demo` -> live), and a single session
may override it, which is how one page can demo the loop and the next page can call the models.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from .llm import GeminiLLM
from .openrouter import OpenRouterLLM
from .router import MultiLLM

ROOT = Path(__file__).resolve().parents[2]


def _models(env: str, default: str) -> list[str]:
    raw = os.getenv(env) or default
    return [m.strip() for m in raw.split(",") if m.strip()]


def build_pool(live: bool, cassette_mode: Optional[str] = None,
               cassette_dir: Optional[str] = None) -> MultiLLM:
    """Every provider that has a key, in preference order.

    The pool is built from what is actually configured: a missing key simply means that provider
    is not a member. Gemini goes first because its free tier is the one that answers reliably at
    this size; OpenRouter's free routes follow and are skipped while rate-limited.
    """
    members: list = []
    mode = cassette_mode or os.getenv("LLM_CASSETTE") or "replay"
    # One member per model string. A provider's internal chain always tries its first model
    # first, so wrapping a chain as a single member would rotate across providers only and
    # answer with the same model every time.
    if os.getenv("GEMINI_API_KEY"):
        for m in _models("GEMINI_MODEL_CHAIN", "gemini-3-flash-preview,gemini-3.1-flash-lite"):
            members.append(GeminiLLM(models=[m], live=live, cassette_mode=mode,
                                     cassette_dir=cassette_dir))
    if os.getenv("OPENROUTER_API_KEY"):
        for m in _models("OPENROUTER_MODEL_CHAIN",
                         os.getenv("OPENROUTER_MODEL") or "nvidia/nemotron-3.5-super-vl:free"):
            members.append(OpenRouterLLM(model=m, live=live, cassette_mode=mode,
                                         cassette_dir=cassette_dir))
    if not members:
        raise RuntimeError("no provider key configured (GEMINI_API_KEY / OPENROUTER_API_KEY)")
    return MultiLLM(members, cooldown_s=float(os.getenv("MODEL_COOLDOWN_S") or "240"),
                    rotate=os.getenv("MODEL_ROTATE", "1") != "0", label="live pool")


def make_llm(live: Optional[bool] = None, cassette_mode: Optional[str] = None,
             backend: Optional[str] = None, layer: Optional[str] = None):
    """`layer` is the session's own choice: `replay` | `live` | `off`."""
    run_mode = (os.getenv("RUN_MODE") or "cached-demo").strip().lower()
    layer = (layer or "").strip().lower()
    if layer == "off":
        from .llm import UnavailableLLM
        return UnavailableLLM()
    if not layer:
        layer = "live" if run_mode == "live-demo" else "replay"
    if live is None:
        live = layer == "live"
    if cassette_mode is None:
        cassette_mode = "record" if (live and run_mode == "live-demo") else \
            (os.getenv("LLM_CASSETTE") or "replay")

    if live:
        # A live pool that first consults the recording is not live: it would answer 463 of the
        # turns from disk. So a live session gets an empty cassette dir unless one is set.
        cassette_dir = os.getenv("LIVE_CASSETTE_DIR") or tempfile.mkdtemp(prefix="live_cass_")
        return build_pool(live=True, cassette_mode=cassette_mode, cassette_dir=cassette_dir)
    if backend == "gemini":
        return GeminiLLM(live=False, cassette_mode=cassette_mode)
    if backend == "openrouter":
        return OpenRouterLLM(live=False, cassette_mode=cassette_mode)
    # Replay through whichever transport the cassettes were recorded against: the cassette key
    # does not include the model, so either one can serve the turn.
    return OpenRouterLLM(live=False, cassette_mode=cassette_mode)


def pool_status(live: bool = False) -> dict:
    """What the UI may honestly print about the models, without spending a request."""
    try:
        pool = build_pool(live=live, cassette_mode="off")
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "members": []}
    return {"ok": True, "members": pool.models, "rotate": pool.rotate,
            "cooldown_s": pool.cooldown_s, "capacity": pool.capacity(),
            "gemini_chain": _models("GEMINI_MODEL_CHAIN", ""),
            "openrouter_chain": _models("OPENROUTER_MODEL_CHAIN",
                                        os.getenv("OPENROUTER_MODEL", ""))}
