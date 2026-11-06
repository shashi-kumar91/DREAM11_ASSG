"""Prove that two SSE journals of the same session are the same run.

`--llm live` spends quota; `--llm replay` must produce the identical artifact, or the recorded
turns are not the product. Wall-clock, ids and the provenance badge are the only differences
allowed, because they are properties of the transport rather than of the answer.

    python backend/scripts/drive_http.py --base http://127.0.0.1:8131 --pack gap --scenario S1 \
        --answer "..." --log backend/out/a.jsonl
    python backend/scripts/drive_http.py --base http://127.0.0.1:8133 --pack gap --scenario S1 \
        --answer "..." --log backend/out/b.jsonl
    python backend/scripts/diff_frames.py backend/out/a.jsonl backend/out/b.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Volatile by construction: when the call happened, which call it was, and which transport served
# it. Everything else is the run's decision and must match. `badges` is dropped from the frame
# comparison because it is asserted separately (that is the whole point of the tool), and the
# question's own wall-clock fields are nested timestamps rather than the frame's `t`.
DROP = {"t", "ms", "elapsed_ms", "duration_ms", "t_asked", "t_answered", "created_at",
        "session_id", "episode_id", "card_id", "id", "trace_id", "run_mode", "bytes_endpoint",
        "sid", "llm_mode"}
PROVENANCE = {"badges", "source_badges"}
ID_RE = re.compile(r"^(?:ep|cd|q|u|pr|tal|evt|frm|clip|s|card|unc|ver|epi|set)_[0-9a-zA-Z]{6,}$")


def _clean(value):
    if isinstance(value, dict):
        out = {}
        for k, v in sorted(value.items()):
            if k in DROP:
                continue
            if k in PROVENANCE and isinstance(v, dict):
                # The badge row is a promise about the fixtures, so every entry but the
                # text-transport one must match exactly.
                out[k] = {b: _clean(x) for b, x in sorted(v.items()) if b != "llm"}
            else:
                out[k] = _clean(v)
        return out
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str) and ID_RE.match(value):
        return "ID"
    return value


def load(path: Path) -> list[tuple[str, str, dict]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        frame = json.loads(line)
        out.append((frame["event"], str(frame["payload"].get("seq", 0)),
                    _clean(frame["payload"])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--expect-badges", default="live,cassette",
                    help="the llm badge of each journal, in order; pass none to skip the check")
    args = ap.parse_args()
    ra, rb = load(Path(args.a)), load(Path(args.b))

    want = [b.strip() for b in args.expect_badges.split(",") if b.strip() and b != "none"]
    for journal, badge in zip((ra, rb), want):
        got = {f[2].get("badges", {}).get("llm") for f in journal if "badges" in f[2]}
        got.discard(None)
        if got and got != {badge}:
            print(f"FAIL: badge mismatch, expected {badge}, saw {sorted(got)}")
            return 1

    cards_a = [f[2]["card"] for f in ra if f[0] == "card"]
    cards_b = [f[2]["card"] for f in rb if f[0] == "card"]
    if len(cards_a) != len(cards_b):
        print(f"FAIL: {len(cards_a)} card(s) vs {len(cards_b)}")
        return 1
    for ca, cb in zip(cards_a, cards_b):
        for field in ("headline", "body", "kind", "truth_label", "cited_event_ids",
                      "cited_tally_ids", "cited_utterance_ids"):
            if ca.get(field) != cb.get(field):
                print(f"FAIL: card field {field}: {ca.get(field)!r} vs {cb.get(field)!r}")
                return 1

    diffs = [(i, x, y) for i, (x, y) in enumerate(zip(ra, rb)) if x != y]
    print(f"frames: {len(ra)} vs {len(rb)} | cards: {len(cards_a)} identical")
    for i, x, y in diffs[:8]:
        print(f"  [{i}] {x[0]}\n     {json.dumps(x[2])[:170]}\n     {json.dumps(y[2])[:170]}")
    if len(ra) != len(rb) or diffs:
        print(f"DIFF: {len(diffs)} frame(s) differ beyond timings, ids and badges")
        return 1
    print("MATCH: same trajectory, same cards, only provenance and wall-clock differ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
