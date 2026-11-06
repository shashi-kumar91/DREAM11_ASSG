"""A pool of real models behind one interface, chosen per call.

Why this exists
---------------
A single hard-coded model string is a single point of failure, and the earlier revision proved
it: the configured model returned empty completions and every episode paid for that twice. A
pool also makes the product less deterministic in the way that matters - different turns are
answered by different models, so a screen that says "the model proposed this" is telling the
truth about a real ensemble rather than one template.

What is and is not rotating
---------------------------
The *transport* rotates across healthy models. The *contract* does not: every member is asked for
the same schema, and whatever comes back is parsed and then judged by the kernel's deterministic
gate. Rotation can change the wording of a proposal; it can never change a number, a label or a
commit.

Health, not hope
----------------
A member that times out, 429s, 404s or returns an empty completion is put on a cooldown and
skipped, so one dead route costs one failed attempt per cooldown window instead of one per turn.
The router reports the model that actually served each turn, so the badge is a fact.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from .llm import LLMError

TModel = TypeVar("TModel", bound=BaseModel)


class MultiLLM:
    """Round-robin over healthy members, with per-member cooldown."""

    backend = "pool"

    def __init__(self, members: list[Any], cooldown_s: float = 240.0,
                 rotate: bool = True, label: str = "pool"):
        if not members:
            raise ValueError("MultiLLM needs at least one member")
        self.members = members
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.rotate = rotate
        self.label = label
        self.calls = 0
        self.mode_used = "none"
        self.last_error: Optional[str] = None
        self.served: list[str] = []              # "gemini-3-flash-preview" per successful turn
        self._dead_until: dict[int, float] = {}
        self._failures: dict[int, int] = {}     # consecutive failures per member
        self._quota: dict[int, float] = {}      # members parked because a daily quota ran out
        self._next = 0
        self.last_chance_used = False

    # -- reporting ---------------------------------------------------------- #

    @property
    def models(self) -> list[str]:
        out: list[str] = []
        for m in self.members:
            name = getattr(m, "model", None) or ", ".join(getattr(m, "models", []) or [])
            out.append(f"{getattr(m, 'backend', '?')}:{name}")
        return out

    def route_status(self) -> dict[str, Any]:
        now = time.time()
        return {"label": self.label, "members": self.models,
                "cooling_down": [self.models[i] for i, until in self._dead_until.items()
                                 if until > now],
                "served_last": self.served[-1] if self.served else "",
                "served_models": sorted(set(self.served)),
                "failures": {self.models[i]: n for i, n in self._failures.items() if n},
                "quota_until": {self.models[i]: int(until) for i, until in self._quota.items()
                                if until > now},
                "capacity": self.capacity(),
                "last_chance_used": self.last_chance_used,
                "live": any(getattr(m, "live", False) for m in self.members)}

    def capacity(self) -> str:
        """One word the page can print: is there any model left to ask?"""
        now = time.time()
        live = [self.models[i] for i in range(len(self.members)) if not self._is_dead(i)]
        if live:
            return "available"
        if any(until > now for until in self._quota.values()):
            return "quota_exhausted"
        return "cooling_down"

    def _order(self) -> list[int]:
        n = len(self.members)
        if not self.rotate:
            return list(range(n))
        start = self._next % n
        self._next = (self._next + 1) % n
        return [(start + i) % n for i in range(n)]

    def _is_dead(self, idx: int) -> bool:
        return self._dead_until.get(idx, 0.0) > time.time()

    def _mark_dead(self, idx: int, why: str) -> None:
        low = why.lower()
        if not any(t in low for t in ("empty", "429", "404", "timeout", "no api key", "unavailable",
                                      "connection", "deadline")):
            return
        # Graded, not absolute. A first failure is usually a burst or a truncated reply; parking
        # the only healthy model for the whole cooldown turned one bad turn into a failed episode
        # (observed live: `pool exhausted` while a model was answering in 0.4 s). So the first
        # failure costs a short pause and only repeated failures earn the full cooldown.
        n = self._failures.get(idx, 0) + 1
        self._failures[idx] = n
        wait = min(self.cooldown_s, 15.0 * (2 ** (n - 1)))
        # A per-day quota is not a hiccup: the free tiers this build runs on allow about 20
        # requests per model per day. Retrying every few minutes burns turns that cannot succeed
        # and hides the real reason from the page, so the member is parked for as long as the
        # provider says, and the status says "quota" rather than "cooling down".
        quota = _quota_wait(why)
        if quota:
            self._quota[idx] = time.time() + quota
            wait = max(wait, quota)
        self._dead_until[idx] = time.time() + wait
        self.last_error = why

    # -- the kernel's only door to a model ---------------------------------- #

    def complete(self, prompt: str, system: str = "", schema: Optional[Type[TModel]] = None,
                 temperature: float = 0.0, max_output_tokens: int = 1400, tag: str = "",
                 key_text: str = "") -> tuple[Any, dict[str, Any]]:
        self.calls += 1
        notes: list[str] = []
        tried: set[int] = set()
        for idx in self._order():
            member = self.members[idx]
            if self._is_dead(idx):
                notes.append(f"{self.models[idx]} cooling down")
                continue
            tried.add(idx)
            try:
                out, meta = member.complete(prompt, system=system, schema=schema,
                                            temperature=temperature,
                                            max_output_tokens=max_output_tokens, tag=tag,
                                            key_text=key_text)
            except LLMError as exc:
                msg = str(exc)
                notes.append(f"{self.models[idx]} -> {msg[:90]}")
                self._mark_dead(idx, msg)
                continue
            except Exception as exc:                       # a bug in one member is not an outage
                notes.append(f"{self.models[idx]} -> {type(exc).__name__}: {exc}")
                self._mark_dead(idx, f"{type(exc).__name__}: {exc}")
                continue
            served = str(meta.get("model") or self.models[idx])
            self.served.append(served)
            self.mode_used = str(meta.get("source") or "live")
            self.last_error = None
            self._failures[idx] = 0
            self._dead_until[idx] = 0.0
            return out, {**meta, "served_by": served, "pool": self.label}
        # Last chance: every member looked unavailable, so the choice is between a guaranteed
        # dead episode and one bounded retry on whichever member has been cooling longest. A
        # quota that has just reset, or a transient 429, recovers here instead of costing the
        # fan their receipt.
        for idx in sorted(self._dead_until, key=lambda i: self._dead_until[i]):
            if self._dead_until.get(idx, 0.0) <= time.time() or idx in tried:
                continue
            member = self.members[idx]
            try:
                out, meta = member.complete(prompt, system=system, schema=schema,
                                            temperature=temperature,
                                            max_output_tokens=max_output_tokens, tag=tag,
                                            key_text=key_text)
            except Exception as exc:
                notes.append(f"{self.models[idx]} last chance failed: {str(exc)[:70]}")
                self._failures[idx] = self._failures.get(idx, 0) + 1
                self._dead_until[idx] = time.time() + self.cooldown_s
                continue
            served = str(meta.get("model") or self.models[idx])
            self.served.append(served)
            self.mode_used = str(meta.get("source") or "live")
            self.last_error = None
            self.last_chance_used = True
            self._failures[idx] = 0
            self._dead_until[idx] = 0.0
            return out, {**meta, "served_by": served, "pool": self.label, "last_chance": True}
        self.mode_used = "unavailable"
        raise LLMError(f"pool exhausted for tag={tag}: " + " | ".join(notes)[:400])

    def audio_text(self, audio_bytes: bytes, mime: str, prompt: str, system: str = "",
                   tag: str = "") -> tuple[str, dict]:
        notes: list[str] = []
        for idx in self._order():
            member = self.members[idx]
            fn = getattr(member, "audio_text", None)
            if fn is None or self._is_dead(idx):
                continue
            try:
                return fn(audio_bytes, mime, prompt, system=system, tag=tag)
            except LLMError as exc:
                notes.append(f"{self.models[idx]} -> {str(exc)[:80]}")
                self._mark_dead(idx, str(exc))
        raise LLMError("no pool member can transcribe audio: " + " | ".join(notes)[:200])


def _quota_wait(why: str) -> float:
    """How long a provider asked us to wait, when the reason is a spent daily quota.

    Google's 429 spells it out ("per day limit exceeded ... Please retry in 17h2m25s") and
    OpenRouter names `free-models-per-day`, so the honest cooldown is the provider's own number
    rather than a guess. Capped at a day: the page should recover on its own tomorrow.
    """
    low = (why or "").lower()
    daily = any(t in low for t in ("per day", "per-day", "daily", "per_day", "free-models-per-day"))
    if not daily and not any(t in low for t in ("429", "rate limit", "quota")):
        return 0.0
    import re as _re
    total = 0.0
    m = _re.search(r"retry in ([0-9]+h)?([0-9]+m)?([0-9.]+ ?s)?", low)
    if m and any(m.groups()):
        if m.group(1):
            total += float(m.group(1)[:-1]) * 3600
        if m.group(2):
            total += float(m.group(2)[:-1]) * 60
        if m.group(3):
            total += float(m.group(3).strip()[:-1])
    if total <= 0 and daily:
        total = 3600.0
    return min(86400.0, total) if total > 0 else 0.0
