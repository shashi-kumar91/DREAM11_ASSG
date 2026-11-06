"""Server-side byte metering.

Why this module exists: the whole "we only load the clip the fan actually asked for" claim is
worth nothing if the *client* reports the byte count. The counter here is the server's own:
every byte the media route puts on the wire passes through `counted_stream`, and the running
total is cumulative per session (one meter per session, never shared, never reset by a reload).

Two numbers are kept on purpose:

* `total_bytes`  - what was really served while this session watched.
* `would_have_kb` - what the same session would have cost if every clip the agent *offered*
  had been loaded. The UI draws both lines; that gap is the product's data argument.

`quiet` is a server behaviour, not a CSS class: a quiet session's meter refuses to load a
single byte and the refusal itself is recorded, with its price, so the jury can see the cost
that was avoided.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Iterator, Optional


class ByteMeter:
    """Cumulative, thread-safe byte counter owned by one HTTP session."""

    def __init__(self, on_record: Optional[Callable[[dict], None]] = None) -> None:
        self.on_record = on_record
        self._lock = threading.Lock()
        self.records: list[dict] = []
        self.total_bytes = 0
        self.refused_bytes = 0
        self.would_have_bytes = 0
        self.clip_loads = 0
        self.refusals = 0

    # ------------------------------------------------------------------ #
    # accounting
    # ------------------------------------------------------------------ #

    @staticmethod
    def kb(n: int) -> float:
        return round(n / 1024.0, 1)

    def price_clip(self, seconds: float, kb_per_second: float) -> int:
        """Bytes a clip of this shape would cost, using the pack's own quotation profile."""
        return int(round(seconds * kb_per_second * 1024))

    def note_offered_clip(self, event_id: str, kb: float, why: str = "") -> None:
        """An agent priced a clip. Counted as debt the fan did not have to pay."""
        with self._lock:
            self.would_have_bytes += int(round(kb * 1024))
        self._emit({"endpoint": f"/api/media/clip/{event_id}", "bytes_sent": 0, "kb": 0.0,
                    "reason": f"clip offered, not loaded ({why or 'fan did not tap'})",
                    "offered_kb": kb,
                    "total_kb": self.kb(self.total_bytes),
                    "would_have_kb": self.kb(self.would_have_bytes)})

    def refuse(self, endpoint: str, would_be_bytes: int, reason: str) -> dict:
        """A load that never happened. Zero bytes charged, the price still recorded."""
        with self._lock:
            self.refusals += 1
            self.refused_bytes += would_be_bytes
            self.would_have_bytes += would_be_bytes
            frame = {"endpoint": endpoint, "bytes_sent": 0, "kb": 0.0, "reason": reason,
                     "refused_kb": self.kb(would_be_bytes),
                     "total_kb": self.kb(self.total_bytes),
                     "would_have_kb": self.kb(self.would_have_bytes)}
        self._emit(frame)
        return frame

    def add(self, endpoint: str, n: int, reason: str, **extra) -> dict:
        with self._lock:
            self.total_bytes += n
            self.would_have_bytes += n
            frame = {"endpoint": endpoint, "bytes_sent": n, "kb": self.kb(n),
                     "reason": reason,
                     "total_kb": self.kb(self.total_bytes),
                     "would_have_kb": self.kb(self.would_have_bytes)}
            frame.update(extra)
        self._emit(frame)
        return frame

    def counted_stream(self, chunks: Iterable[bytes], endpoint: str, reason: str,
                       **extra) -> Iterator[bytes]:
        """Wrap a chunk iterable; the bytes that actually leave are the bytes counted.

        A client that hangs up mid-download gets charged for what it received and nothing
        more, because the `finally` records the partial total rather than the file size.
        """
        sent = 0
        try:
            for chunk in chunks:
                if not chunk:
                    continue
                sent += len(chunk)
                yield chunk
        finally:
            if sent:
                with self._lock:
                    self.clip_loads += 1
                    self.total_bytes += sent
                    self.would_have_bytes += sent
                    frame = {"endpoint": endpoint, "bytes_sent": sent, "kb": self.kb(sent),
                             "reason": reason, "clip_loads": self.clip_loads,
                             "total_kb": self.kb(self.total_bytes),
                             "would_have_kb": self.kb(self.would_have_bytes)}
                    frame.update(extra)
                self._emit(frame)

    def snapshot(self) -> dict:
        with self._lock:
            return {"total_bytes": self.total_bytes, "kb": self.kb(self.total_bytes),
                    "clip_loads": self.clip_loads, "refusals": self.refusals,
                    "refused_kb": self.kb(self.refused_bytes),
                    "would_have_kb": self.kb(self.would_have_bytes),
                    "saved_kb": self.kb(self.would_have_bytes - self.total_bytes),
                    "records": list(self.records)}

    # ------------------------------------------------------------------ #

    def _emit(self, frame: dict) -> None:
        frame = {"t": round(time.time(), 4), **frame}
        with self._lock:
            self.records.append(frame)
        if self.on_record:
            try:
                self.on_record(frame)
            except Exception:      # a dead journal must never break a media stream
                pass
