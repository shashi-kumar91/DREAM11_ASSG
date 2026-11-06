"""OpenRouter transport for the same agent contract as GeminiLLM.

Why this exists: the Gemini key this project was built against is free-tier and caps
`generate_content` at 20 requests/day, which is less than one 12-segment episode needs.
OpenRouter gives the agent somewhere to run when that quota is spent, so "the LLM was
unavailable" never becomes the reason a demo can't show the loop.

Deliberate choices:
* Structured output is requested as `json_object` plus the schema written into the system
  prompt, not `json_schema` with strict mode. A stealth/preview model is free precisely
  because it is under-specified; refusing to send a strict schema costs nothing because
  the pydantic validation retry in the orchestrator already catches a bad shape.
* Cassettes are shared with GeminiLLM and keyed without the model name, so a recorded
  agent turn replays identically whichever backend produced it. The UI badge reports the
  backend that actually served each call from the cassette payload, not the configured one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel

from .llm import Cassette, LLMError, parse_output, retry_delay_from

TModel = TypeVar("TModel", bound=BaseModel)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Cheap, and it works: the usual failure on a stealth-tier model is a prose answer, and the same
# model produces the object when the shape is restated as the last thing it reads.
JSON_REMINDER = ("\n\nANSWER FORM: your entire reply must be one JSON object whose first "
                 'character is an opening brace - no sentences, no markdown, no code fence. '
                 'Any other reply is discarded.')


class OpenRouterLLM:
    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cassette_dir: Optional[str] = None,
        cassette_mode: Optional[str] = None,
        live: bool = True,
        timeout_s: float = 45.0,
    ):
        self.model = model or os.getenv("OPENROUTER_MODEL") or "stealth/crouton-alpha"
        # A free-tier route 502s, times out and truncates. One model means one outage deletes
        # the episode, so the transport rotates a chain and returns whichever model answered.
        # Cassettes are keyed without the model name, so a fallback's answer replays identically.
        chain = os.getenv("OPENROUTER_MODEL_CHAIN", "")
        self.chain = [m.strip() for m in chain.split(",") if m.strip()] or [self.model]
        if self.model not in self.chain:
            self.chain.insert(0, self.model)
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL") or
                         "https://openrouter.ai/api/v1").rstrip("/")
        self.live = live
        self.timeout_s = timeout_s
        self.calls = 0
        # A dead route is a property of the provider, not of this call. Without a cooldown the
        # same empty completion is paid for on every single turn of the episode, which is how a
        # free-tier chain turns a 30-second episode into a five-minute one.
        self._dead_until: dict[str, float] = {}
        self.dead_cooldown_s = float(os.getenv("OPENROUTER_DEAD_COOLDOWN_S") or "600")
        self.dead_models: list[str] = []
        self.last_error: Optional[str] = None
        self.mode_used = "none"
        self.backend = "openrouter"
        self.models = [self.model]
        cassette_dir = cassette_dir or os.getenv(
            "LLM_CASSETTE_DIR") or str(Path(__file__).resolve().parents[2] / "tests" / "cassettes")
        self.cassette = Cassette(Path(cassette_dir),
                                 cassette_mode or os.getenv("LLM_CASSETTE") or "replay")
        self._stale_keys: set[str] = set()

    # -- core -------------------------------------------------------------- #

    def complete(
        self,
        prompt: str,
        system: str = "",
        schema: Optional[Type[TModel]] = None,
        temperature: float = 0.0,
        max_output_tokens: int = 1400,
        tag: str = "",
        key_text: str = "",
    ) -> tuple[Any, dict[str, Any]]:
        self.calls += 1
        schema_json = json.dumps(schema.model_json_schema(), sort_keys=True) if schema else ""
        # A retry prompt differs from the original only by the kernel's correction nudge, so it
        # must record under the ORIGINAL key - otherwise a provider hiccup permanently removes
        # that turn from the offline replay path.
        ck = self.cassette.key(system, key_text or prompt, schema_json, self.model, temperature)

        cached = self.cassette.load(ck)
        if cached is not None:
            try:
                parsed = parse_output(cached, schema, tag)
            except LLMError as exc:
                # An entry that cannot be replayed is worse than no entry: it turns a one-off
                # truncated recording into a permanent failure. So re-record it - but only
                # overwrite once the replacement actually exists. Deleting first lost a real
                # recording when the re-record failed on a spent daily quota, and the path it
                # served stayed broken until somebody noticed.
                self._stale_keys.add(ck)
                if not self.live:
                    raise LLMError(f"cassette entry for tag={tag} does not parse: {exc}",
                                   cassette_miss=True)
            else:
                self.mode_used = "cassette"
                return parsed, {
                    "source": "cassette", "model": "recorded", "tag": tag, "cached": True}

        if os.environ.get("CASSETTE_DEBUG_ALL"):
            import hashlib as _h
            dbg = Path(os.environ.get("CASSETTE_DEBUG_DIR", "/tmp/misses"))
            dbg.mkdir(parents=True, exist_ok=True)
            body = (f"BACKEND openrouter TAG {tag}\nKEY {ck}\nHIT {cached is not None}\n"
                    f"SYSTEM\n{system}\n\nPROMPT\n{key_text or prompt}\n")
            stem = f"all-{tag or 'untagged'}-{_h.sha1(body.encode()).hexdigest()[:8]}"
            (dbg / f"{stem}.txt").write_text(body)
            # The exact call, byte for byte: a miss has to be reproducible later (re-record it
            # with the same key) and the human-readable dump above trims whitespace.
            (dbg / f"{stem}.json").write_text(json.dumps(
                {"key": ck, "tag": tag, "system": system, "key_text": key_text or prompt,
                 "schema_json": schema_json, "temperature": temperature}, indent=1))
        if not self.live and os.environ.get("CASSETTE_DEBUG_MISS"):
            import hashlib as _h
            dbg = Path(os.environ.get("CASSETTE_DEBUG_DIR", "/tmp/misses"))
            dbg.mkdir(parents=True, exist_ok=True)
            body = (f"BACKEND openrouter TAG {tag}\nKEY {ck}\nSYSTEM\n{system}\n\n"
                    f"PROMPT\n{key_text or prompt}\n")
            (dbg / f"or-{tag or 'untagged'}-{_h.sha1(body.encode()).hexdigest()[:8]}.txt").write_text(body)
        if not self.live:
            # The badge has to say what happened: no model turn, not a live one.
            self.mode_used = "unavailable"
            raise LLMError(f"live disabled, no cassette for tag={tag}", cassette_miss=True)
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY missing")

        if not isinstance(self._dead_until, dict):
            self._dead_until = {}
        notes: list[str] = []
        now = time.time()
        for model in self.chain:
            if self._dead_until.get(model, 0.0) > now:
                notes.append(f"{model} -> cooling down after a dead attempt")
                continue
            # A free-tier model that answers in prose is the most common failure, and it is
            # usually cured by asking again in its own words - so each model gets one stricter
            # attempt before the chain rotates. The cassette key is computed above, from the
            # untouched system text, so the nudge never pollutes the replay path.
            for nudge in ("", JSON_REMINDER):
                try:
                    text, usage, served = self._post(model, prompt, system + nudge, schema,
                                                     schema_json, temperature,
                                                     max_output_tokens, tag)
                except LLMError as exc:
                    notes.append(f"{model} -> {str(exc)[:70]}")
                    self.last_error = str(exc)
                    self._mark_dead(model, str(exc))
                    break
                try:
                    parsed = parse_output(text, schema, tag)
                except LLMError:
                    # A reply that will not parse is not worth recording: storing it would turn
                    # one bad completion into a permanent replay failure.
                    notes.append(f"{model} -> unparseable "
                                 f"(finish_reason={usage.get('finish_reason', '?')}, "
                                 f"out={usage.get('output_tokens', '?')})")
                    continue
                self.mode_used = "live"
                self.last_error = None
                self._dead_until.pop(model, None)
                self.cassette.save(ck, text, served, usage)
                self._stale_keys.discard(ck)
                return parsed, {"source": "live", "model": served, "tag": tag, "usage": usage,
                                "cached": False, "chain_tries": len(notes) + 1}
        # The badge is a promise about provenance, so a dead chain has to be recorded as dead
        # rather than left at "none" - the card then says which run it came from.
        self.mode_used = "unavailable"
        raise LLMError(f"openrouter chain failed for tag={tag}: " + " | ".join(notes)[:300])

    def _mark_dead(self, model: str, why: str) -> None:
        """Remember a route that just proved unusable, so the next turn does not pay for it.

        Only provider-side failure counts: an unparseable answer is retried in the same turn by
        design, and a truncation is a budget bug that a bigger cap usually cures.
        """
        low = why.lower()
        if not ("empty completion" in low or "404" in low or "429" in low or "timeout" in low
                or "missing" in low):
            return
        self._dead_until[model] = time.time() + self.dead_cooldown_s
        if model not in self.dead_models:
            self.dead_models.append(model)

    def route_status(self) -> dict[str, Any]:
        """What the UI may honestly print about which model actually answers."""
        now = time.time()
        return {"preferred": self.model,
                "chain": list(self.chain),
                "cooling_down": [m for m in self.chain if self._dead_until.get(m, 0) > now],
                "dead_models": list(self.dead_models)}

    # -- transport --------------------------------------------------------- #

    def _messages(self, system: str, prompt: str, schema: Optional[Type[BaseModel]],
                  schema_json: str) -> list[dict[str, str]]:
        head = system or "You are a bounded sports agent."
        if schema is not None:
            head += ("\n\nReply with ONE JSON object and nothing else. It must validate "
                     f"against this schema:\n{schema_json}")
        return [{"role": "system", "content": head}, {"role": "user", "content": prompt}]

    def _post(self, model: str, prompt: str, system: str, schema: Optional[Type[BaseModel]],
              schema_json: str, temperature: float, max_output_tokens: int,
              tag: str) -> tuple[str, dict[str, int], str]:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(system, prompt, schema, schema_json),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}

        last = ""
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    r = client.post(f"{self.base_url}/chat/completions",
                                    headers={"Authorization": f"Bearer {self.api_key}",
                                             "Content-Type": "application/json",
                                             "HTTP-Referer": "https://localhost/dre-agent",
                                             "X-Title": "dre-bounded-sports-agent"},
                                    json=body)
            except httpx.TimeoutException:
                last = f"timeout after {self.timeout_s}s"
                continue
            if r.status_code == 200:
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                text = ((choice.get("message") or {}).get("content") or "").strip()
                u = data.get("usage") or {}
                usage = {"prompt_tokens": int(u.get("prompt_tokens") or 0),
                         "output_tokens": int(u.get("completion_tokens") or 0),
                         "finish_reason": choice.get("finish_reason") or ""}
                if not text:
                    # A stealth-tier model returns an empty message when it spends the whole
                    # budget on reasoning. That is worth one retry, not a dead episode.
                    last = "empty completion"
                    if attempt < 1:
                        body["max_tokens"] = int(max_output_tokens * 1.6)
                        time.sleep(1.0)
                        continue
                    break
                if usage["finish_reason"] == "length" and attempt < 1:
                    # Cut off by the token cap, not by the model's intent: the same model with a
                    # bigger budget usually finishes the object. Cheaper than rotating.
                    last = f"truncated at {usage['output_tokens']} output tokens"
                    body["max_tokens"] = int(body["max_tokens"] * 1.8)
                    continue
                return text, usage, data.get("model") or model
            last = f"{r.status_code}: {r.text[:200]}"
            if r.status_code == 429:
                delay = _retry_after(r) or retry_delay_from(r.text) or (2.0 * (attempt + 1))
                if attempt < 2 and delay <= 30:
                    time.sleep(delay)
                    continue
            elif r.status_code in RETRYABLE_STATUS and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        self.last_error = last
        raise LLMError(f"openrouter failed for tag={tag} model={model}: {last}", last_status=last)

    # -- audio is not this backend's job ----------------------------------- #

    def audio_text(self, audio_bytes: bytes, mime: str, prompt: str, system: str = "",
                   tag: str = "") -> tuple[str, dict]:
        ck = "audio_" + hashlib.sha256(audio_bytes + prompt.encode()).hexdigest()[:32]
        cached = self.cassette.load(ck)
        if cached is not None:
            self.mode_used = "cassette"
            return cached, {"source": "cassette", "cached": True, "tag": tag}
        raise LLMError(
            "audio transcription needs the Gemini backend; no cassette for this segment",
            cassette_miss=True)


def _retry_after(r: httpx.Response) -> Optional[float]:
    raw = r.headers.get("retry-after") or r.headers.get("Retry-After")
    if not raw:
        return None
    m = re.search(r"\d+(\.\d+)?", raw)
    return float(m.group()) if m else None
