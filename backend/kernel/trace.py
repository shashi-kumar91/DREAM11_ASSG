"""Agent trace: one JSONL line per observation, dispatch, tool call, verdict and commit.

The trace is the demo's primary evidence. It is written unconditionally, including for
blocked tool calls and guardrail rejections, because "the agent tried to leak a future
event and the clamp stopped it" is a result worth showing.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .schemas import now


class TraceRecorder:
    def __init__(self, path: Optional[Path] = None, on_event: Optional[Callable[[dict], None]] = None):
        self.path = path
        self.on_event = on_event
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "t": round(now(), 4), "kind": kind, **payload}
            self.events.append(ev)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass
        return ev

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e["kind"]] = out.get(e["kind"], 0) + 1
        return out

    @staticmethod
    def load(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
