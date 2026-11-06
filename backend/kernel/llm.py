"""Gemini LLM adapter with model fallback, strict JSON-schema mode and record/replay cassettes.

Design notes
------------
* Model chain is config-driven because vendor availability changes: the key this project
  was built against had `gemini-2.5-flash` retired and Pro quota-blocked, so Flash tiers
  are the default and every call degrades across the chain before it fails.
* Cassettes make the agent reproducible offline (AT-14) and give demo day a safe fallback.
  The UI always badges whether the LLM is live or recorded; fallbacks are never hidden.
* Nothing here knows about cards or packs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .schemas import now

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_MODEL_CHAIN = ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite"]
RETRYABLE = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DeadlineExceeded")


class LLMError(RuntimeError):
    def __init__(self, msg: str, *, cassette_miss: bool = False, last_status: str = ""):
        super().__init__(msg)
        self.cassette_miss = cassette_miss
        self.last_status = last_status


def _env_models() -> list[str]:
    raw = os.getenv("GEMINI_MODEL_CHAIN", "")
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODEL_CHAIN)


def parse_output(text: str, schema: Optional[Type[BaseModel]], tag: str) -> Any:
    """Tolerant JSON extraction. Models wrap objects in fences or prose far more often than
    they invent numbers, so the parser does the boring recovery and the validator does the
    real work."""
    if schema is None:
        return text
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.M).strip()
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        try:
            return schema.model_validate(json.loads(match.group(0)))
        except Exception:
            pass
    raise LLMError(f"unparseable JSON for tag={tag}: {cleaned[:200]}")


RETRY_DELAY_RE = re.compile(r"retry in ([\dhms]+)", re.I)


def retry_delay_from(msg: str) -> Optional[float]:
    """Free-tier providers say when you may come back; ignoring that turns one blocked
    episode into a demo-day hang."""
    m = RETRY_DELAY_RE.search(msg or "")
    if not m:
        return None
    total, num, unit = 0.0, "", ""
    for num, unit in re.findall(r"(\d+)([hms])", m.group(1), re.I):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit.lower()]
    return total or None


def _canonical(value: Any) -> str:
    if isinstance(value, type) and issubclass(value, BaseModel):
        return json.dumps(value.model_json_schema(), sort_keys=True, default=str)
    if isinstance(value, BaseModel):
        return value.model_dump_json(exclude_none=True)
    return json.dumps(value, sort_keys=True, default=str)


class Cassette:
    """Content-addressed store of raw LLM completions."""

    def __init__(self, directory: Path, mode: str):
        self.dir = directory
        self.mode = mode  # record | replay | off
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def key(self, system: str, prompt: str, schema: str, model: str, temp: float) -> str:
        # model excluded from the key so a chain fallback still hits the cassette
        payload = "\x00".join([system, prompt, schema, f"{temp:.2f}"])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def path(self, k: str) -> Path:
        return self.dir / f"{k}.json"

    def load(self, k: str) -> Optional[str]:
        p = self.path(k)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("text")

    def drop(self, k: str) -> None:
        """Forget an entry that no longer validates - a poisoned recording must not replay."""
        with self._lock:
            self.path(k).unlink(missing_ok=True)

    def save(self, k: str, text: str, model: str, usage: dict[str, int]) -> None:
        if self.mode != "record":
            return
        with self._lock:
            self.path(k).write_text(
                json.dumps({"text": text, "model": model, "usage": usage, "t": now()}, indent=2),
                encoding="utf-8",
            )


class UnavailableLLM:
    """The genuinely dead transport: the third layer a reviewer has to be able to see alone.

    `OpenRouterLLM(live=False)` is not this - it still replays every recorded turn and badges
    `cassette`. Nothing here can hand back a model's words, so a card committed under this
    transport is provably the deterministic fallback's, and the badge says so.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.last_error: Optional[str] = None
        self.mode_used = "unavailable"
        self.live = False
        self.backend = "unavailable"

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
        self.mode_used = "unavailable"
        self.last_error = "provider unavailable"
        raise LLMError("provider unavailable")


class GeminiLLM:
    def __init__(
        self,
        models: Optional[list[str]] = None,
        cassette_dir: Optional[str] = None,
        cassette_mode: Optional[str] = None,
        api_key: Optional[str] = None,
        live: bool = True,
    ):
        self.models = models or _env_models()
        self.model = self.models[0] if self.models else ""     # what the router reports
        self.backend = "gemini"
        self.live = live
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.calls = 0
        self.last_error: Optional[str] = None
        self.mode_used = "live"
        self._client = None
        self._client_failed = False
        cassette_dir = cassette_dir or os.getenv(
            "LLM_CASSETTE_DIR") or str(Path(__file__).resolve().parents[2] / "tests" / "cassettes")
        self.cassette = Cassette(Path(cassette_dir), cassette_mode or os.getenv("LLM_CASSETTE") or "replay")

    # -- client ------------------------------------------------------------ #

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self._client_failed or not self.api_key:
            raise LLMError("no api key configured")
        from google import genai

        self._client = genai.Client(api_key=self.api_key)
        return self._client

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
        """Return (parsed pydantic model | str, meta). Raises LLMError when nothing worked."""
        self.calls += 1
        schema_json = _canonical(schema) if schema else ""
        # Retry nudges record under the base prompt so a replayed run needs no retry to work.
        ck = self.cassette.key(system, key_text or prompt, schema_json, self.models[0], temperature)

        cached = self.cassette.load(ck)
        if cached is not None:
            self.mode_used = "cassette"
            meta = {"source": "cassette", "model": "recorded", "tag": tag, "cached": True}
            return self._parse(cached, schema, tag), meta

        if os.environ.get("CASSETTE_DEBUG_MISS"):
            # Off by default. Turned on only when a replay run misses and the reason has to be
            # seen rather than guessed: the exact prompt that had no recording, on disk.
            import hashlib as _h
            dbg = Path(os.environ.get("CASSETTE_DEBUG_DIR", "/tmp/misses"))
            dbg.mkdir(parents=True, exist_ok=True)
            body = f"TAG {tag}\nKEY {ck}\nSYSTEM\n{system}\n\nPROMPT\n{key_text or prompt}\n"
            (dbg / f"{tag or 'untagged'}-{_h.sha1(body.encode()).hexdigest()[:8]}.txt").write_text(body)
        if self.cassette.mode == "replay" and not self.live:
            self.mode_used = "unavailable"
            raise LLMError(f"cassette miss for tag={tag}", cassette_miss=True)
        if not self.live:
            self.mode_used = "unavailable"
            raise LLMError(f"live calls disabled and no cassette for tag={tag}", cassette_miss=True)

        statuses = []
        for model in self.models:
            for attempt in range(2):
                try:
                    text, usage = self._call_live(model, system, prompt, schema, temperature, max_output_tokens)
                    parsed = self._parse(text, schema, tag)
                    # Record only what parses: a truncated reply replayed from a cassette is a
                    # permanent failure rather than a retryable one.
                    self.cassette.save(ck, text, model, usage)
                    self.mode_used = "live"
                    self.last_error = None
                    meta = {"source": "live", "model": model, "tag": tag, "usage": usage, "cached": False}
                    return parsed, meta
                except ValidationError as exc:
                    statuses.append(f"{model}:badjson")
                    if attempt == 0 and schema is not None:
                        prompt = prompt + "\n\nReturn ONLY a valid JSON object matching the given schema."
                        continue
                    raise LLMError(f"model output invalid for tag={tag}: {exc}", last_status=";".join(statuses))
                except Exception as exc:  # provider-level
                    msg = f"{type(exc).__name__}: {str(exc)[:200]}"
                    statuses.append(f"{model}:{msg[:80]}")
                    retryable = any(s in str(exc).upper() for s in RETRYABLE)
                    if retryable and attempt == 0:
                        time.sleep(1.2 * (attempt + 1))
                        continue
                    break
        self.last_error = ";".join(statuses)
        raise LLMError(f"all models failed for tag={tag}: {self.last_error}", last_status=self.last_error)

    def _call_live(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: Optional[Type[BaseModel]],
        temperature: float,
        max_output_tokens: int,
    ) -> tuple[str, dict[str, int]]:
        from google.genai import types

        client = self._get_client()
        cfg_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "response_mime_type": "application/json" if schema else "text/plain",
        }
        if system:
            cfg_kwargs["system_instruction"] = system
        budget = (os.getenv("GEMINI_THINKING_BUDGET") or "0").strip()
        if budget:                       # "0" disables thinking: 62 s -> 7 s on the same prompt
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))
            except Exception:
                pass
        if schema is not None:
            try:
                cfg_kwargs["response_schema"] = schema
            except Exception:
                cfg_kwargs["response_schema"] = schema.model_json_schema()
        config = types.GenerateContentConfig(**cfg_kwargs)
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
        text = (resp.text or "").strip()
        um = getattr(resp, "usage_metadata", None)
        usage = {
            "prompt_tokens": int(getattr(um, "prompt_token_count", 0) or 0),
            "output_tokens": int(getattr(um, "candidates_token_count", 0) or 0),
        }
        if not text:
            raise RuntimeError("empty response")
        return text, usage

    # -- helpers ----------------------------------------------------------- #

    def _parse(self, text: str, schema: Optional[Type[TModel]], tag: str) -> Any:
        return parse_output(text, schema, tag)

    def audio_text(self, audio_bytes: bytes, mime: str, prompt: str, system: str = "", tag: str = "") -> tuple[str, dict]:
        """Transcribe/understand inline audio. Kept separate because ASR needs a Part, not a schema."""
        self.calls += 1
        ck = "audio_" + hashlib.sha256(audio_bytes + prompt.encode()).hexdigest()[:32]
        cached = self.cassette.load(ck)
        if cached is not None:
            self.mode_used = "cassette"
            return cached, {"source": "cassette", "cached": True, "tag": tag}
        if not self.live:
            raise LLMError(f"live disabled, no audio cassette for tag={tag}", cassette_miss=True)
        from google.genai import types

        client = self._get_client()
        for model in self.models:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_bytes(data=audio_bytes, mime_type=mime),
                        types.Part.from_text(text=prompt),
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.0, system_instruction=system or None, max_output_tokens=2000
                    ),
                )
                text = (resp.text or "").strip()
                if not text:
                    continue
                self.cassette.save(ck, text, model, {})
                self.mode_used = "live"
                return text, {"source": "live", "model": model, "cached": False, "tag": tag}
            except Exception as exc:
                self.last_error = f"{model}: {str(exc)[:120]}"
                continue
        raise LLMError(f"audio call failed for tag={tag}: {self.last_error}")
