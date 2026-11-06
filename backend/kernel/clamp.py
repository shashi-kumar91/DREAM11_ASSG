"""Spoiler clamp: the only feed surface any agent or tool may touch.

`resume_cursor` is the fan's playback position. Anything at a higher index is, by
construction, unqueryable. There is no code path that hands out the raw list.
"""
from __future__ import annotations

from typing import Optional

from .schemas import MatchEvent


class ClampedFeed:
    def __init__(self, events: list[MatchEvent], resume_cursor: Optional[str] = None):
        self._events = list(events)
        self._by_id = {e.id: e for e in self._events}
        self._index = {e.id: i for i, e in enumerate(self._events)}
        self.resume_cursor = resume_cursor or (self._events[-1].id if self._events else "")
        self.queries = 0
        self.clamped_hits = 0

    # -- cursor ------------------------------------------------------------ #

    @property
    def cursor_index(self) -> int:
        return self._index.get(self.resume_cursor, len(self._events) - 1)

    def set_cursor(self, event_id: str) -> bool:
        if event_id in self._index:
            self.resume_cursor = event_id
            return True
        return False

    def advance_to(self, event_id: str) -> None:
        self.set_cursor(event_id)

    # -- queries ----------------------------------------------------------- #

    def get(self, event_id: str) -> Optional[MatchEvent]:
        self.queries += 1
        e = self._by_id.get(event_id)
        if e is None or self._index[e.id] > self.cursor_index:
            self.clamped_hits += 1
            return None
        return e

    def between(self, start_id: Optional[str] = None, end_id: Optional[str] = None,
                inclusive_end: bool = True) -> list[MatchEvent]:
        """Events in [start, end], hard-clamped at the resume cursor."""
        self.queries += 1
        lo = self._index[start_id] if start_id in self._index else 0
        if end_id in self._index:
            hi = self._index[end_id] + (1 if inclusive_end else 0)
        else:
            hi = self.cursor_index + 1
        asked_hi = hi - 1
        hi = min(hi, self.cursor_index + 1)
        if asked_hi > self.cursor_index:
            self.clamped_hits += 1
        return self._events[lo:hi]

    def upto_cursor(self) -> list[MatchEvent]:
        self.queries += 1
        return self._events[: self.cursor_index + 1]

    def latest(self, n: int = 5) -> list[MatchEvent]:
        return self.upto_cursor()[-n:]

    def first_at_or_after(self, over_label: str) -> Optional[MatchEvent]:
        for e in self.upto_cursor():
            if _over_key(e.over_label) >= _over_key(over_label):
                return e
        return None

    def in_over(self, over: int) -> list[MatchEvent]:
        return [e for e in self.upto_cursor() if e.over_label.split(".")[0] == str(over)]

    @property
    def stats(self) -> dict[str, int]:
        return {"queries": self.queries, "clamped_hits": self.clamped_hits}


def _over_key(label: str) -> tuple[int, int]:
    parts = label.replace(" ", "").split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)
