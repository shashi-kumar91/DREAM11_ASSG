"""ASR adapters.

Two implementations behind one interface so the demo never dies with a vendor:
  * GeminiASR  - real audio -> text, with keyterm boosting
  * CachedASR  - the fixture transcript, honestly badged "ASR: cached" in the UI

Confidence caveat: Gemini does not emit an acoustic confidence score, so `confidence` is
the model's own calibrated self-report of word-level certainty. The UI labels it as
self-reported. It is used only to gate abstention, never to assert a fact.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

ASR_PROMPT = """You are transcribing one short clip of live Hinglish cricket commentary
(Hindi written in Latin script, mixed with English). Transcribe EXACTLY what is said.

Rules:
- Do not translate. Do not tidy grammar. Do not add words that were not spoken.
- Keep numbers as spoken ("142/4", "do yorker", "2.5 millimeter").
- If the audio is unclear, transcribe your best guess but lower `confidence` and list the
  unclear words in `uncertain_words`. Never invent a player name.
- Ignore the crowd-noise bed.

Reply with ONLY this JSON object:
{"text": "<verbatim transcription>",
 "confidence": <0.0-1.0, your certainty that every word is right>,
 "uncertain_words": ["<word>", ...]}

Boost list (prefer these spellings only if you clearly hear them): {keyterms}
"""


class AsrResult(BaseModel):
    text: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    uncertain_words: list[str] = Field(default_factory=list)
    source: str = "cached"
    model: str = ""


class CachedASR:
    source = "cached"

    def __init__(self, path: Path | None = None):
        self.rows = json.loads((path or FIXTURES / "transcript_expected.json")
                               .read_text(encoding="utf-8"))
        self.by_id = {r["id"]: r for r in self.rows}

    def transcribe(self, audio: bytes, keyterms: list[str], hint_id: str = "") -> AsrResult:
        if hint_id and hint_id in self.by_id:
            r = self.by_id[hint_id]
            return AsrResult(text=r["text"], confidence=r["asr_confidence_fixture"],
                             source="cached", model="fixture")
        return AsrResult(text="", confidence=0.0, source="cached", model="fixture")

    def segments(self) -> list[dict]:
        return self.rows


class GeminiASR:
    source = "live"

    def __init__(self, llm):
        self.llm = llm

    def transcribe(self, audio: bytes, keyterms: list[str], hint_id: str = "",
                   mime: str = "audio/wav") -> AsrResult:
        prompt = ASR_PROMPT.replace("{keyterms}", ", ".join(keyterms[:60]))
        raw, meta = self.llm.audio_text(audio, mime, prompt, tag=f"asr:{hint_id}")
        text = _extract_json(raw)
        if text is None:
            return AsrResult(text=raw.strip()[:300], confidence=0.5, source="live",
                             model=meta.get("model", ""),
                             uncertain_words=["unstructured_asr_response"])
        try:
            obj = json.loads(text)
            return AsrResult(text=str(obj.get("text", "")).strip(),
                             confidence=float(obj.get("confidence", 0.8)),
                             uncertain_words=[str(w) for w in obj.get("uncertain_words", [])][:20],
                             source="live", model=meta.get("model", ""))
        except (ValueError, TypeError, AttributeError):
            return AsrResult(text=raw.strip()[:300], confidence=0.5, source="live",
                             model=meta.get("model", ""))


def _extract_json(raw: str) -> str | None:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.S)
    return m.group(0) if m else None
