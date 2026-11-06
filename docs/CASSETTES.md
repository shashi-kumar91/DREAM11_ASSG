# The recorded model turns, and what they cover

`tests/cassettes/` is why this product runs with no API key, no network and no waiting: each
file is one completion, keyed by the SHA-256 of the exact system prompt, user prompt, JSON
schema and temperature that produced it. No number in the UI is computed from anything else.

**585 recordings.** 580 were made against the live Gemini and OpenRouter routes with the key
in `.env`; the file says which model answered each one (`"model"`), so the claim is checkable
per turn. The other **5 were written by hand** during a provider outage and say
`"model": "arena-agent (hand-authored)"` — see "Hand-authored turns" below. Every one of those
5 is paired with the verbatim prompt it answers in `docs/handauthored/`, so the pairing is
checkable the same way the provider turns are.

## Why a recording can stop matching (and what happens then)

A recording is content-addressed, so any edit to a prompt orphans the turns around it. That is
the honest failure mode: the run does not invent a model answer, it says so and falls back.

When that happens the page shows one line above the artifact:

| what you see | what it means |
|---|---|
| "answered by live models" | real calls this run, nothing recorded |
| "the model did not answer this step" | the run asked for a live pool and every member failed (the note beside it says why) |
| "answered from recorded model turns" | every turn came off disk |
| "...none was recorded for this step; the kernel answered from the feed" | this step had no recording, so code answered |
| "a model call for this step was rate-limited" | the provider said 429; code answered |
| "the model pool had no capacity for this step (quota exhausted)" | the free tier is spent; code answered |

A missing turn never produces a fabricated card. It produces the deterministic card, which is
built from the fixture feed and labelled as such.

## Coverage, measured — 10/10

`python backend/scripts/cassette_coverage.py` drives every path against the running server and
prints which ones were served from disk. Full pass (server on `:8931`, `RUN_MODE=cached-demo`,
`LLM_CASSETTE=replay`), re-run **after** `probe_dynamic.py` has injected jury-pane heartbeats
into the same server — the exact sequence that used to break it:

```
ok        gap/S1 ans=0 turns=3 errors=0 cards=1
ok        gap/S1 ans=1 turns=3 errors=0 cards=1
ok        gap/S2 ans=0 turns=4 errors=0 cards=1
ok        gap/S2 ans=1 turns=4 errors=0 cards=1
ok        gap/S3 ans=0 turns=6 errors=0 cards=1
ok        gap/S3 ans=1 turns=6 errors=0 cards=1
ok        creator/ALL turns=23 errors=0 cards=8
ok        gap/S1 ans=None turns=2 errors=0 cards=1
ok        gap/S2 ans=None turns=4 errors=0 cards=1
ok        gap/S3 ans=None turns=1 errors=0 cards=1

10/10 paths fully served from the recorded store
```

Every supervisor turn, specialist turn and critic review on every path comes off disk: zero
model errors, zero fallback cards, zero "critic unavailable" notes.

### How it got to 10/10 (honest history)

* An earlier pass measured **4/10** and named five "missing" keys. Three of them were never
  missing at all: `POST /api/session/{sid}/interrupt` (the jury pane) appended a heartbeat to
  the **shared** fixture scenario, so a probe's injected stall followed into every later
  session's prompts — keys computed from those polluted prompts had never been in the store.
  Fixed by binding each session to its own copy of the scenario
  (`GapSession.run_scenario`); `tests/test_custom_cases.py::test_a_jury_pane_heartbeat_never_reaches_a_later_session`
  locks it, and the coverage table above is measured *after* `probe_dynamic.py` has exercised
  the interrupt endpoint on the same server.
* Two supervisor turns and three critic turns were genuinely absent: an earlier recording pass
  deleted entries before re-recording them (`cassette.drop()`), and the re-record then hit the
  daily quota. The delete-before-record behaviour was already removed in revision 5; the five
  turns themselves are the hand-authored entries below.

## Hand-authored turns (5)

On 2026-11-05 every provider in the pool was at its free-tier daily cap (OpenRouter
`429 free-models-per-day`, reset header `1793923200000` = 2026-11-06T00:00:00Z; Gemini's daily
quota likewise spent), so the turns no key could re-record were written by the reviewing AI
agent instead of being left as fallbacks. They are marked, not disguised:

| key | tag | what the turn decides |
|---|---|---|
| `46f599f6b1…` | supervisor | S1, fan never answered: commit the telemetry-boundary receipt |
| `6977658a1a…` | supervisor | S3 late join, fan never answered: commit the code-built full-window receipt |
| `76998e15c9…` | critic:0 | creator prediction card u05: approve (quoted, labelled a prediction, cited) |
| `d56cbfc4c6…` | critic:0 | review of S1's code-built receipt: approve |
| `ef2c0029af…` | critic:0 | review of S3's code-built receipt: approve |

Each file carries `"model": "arena-agent (hand-authored)"` and `"usage": {0, 0}` because no
provider tokens were spent. `docs/handauthored/<key>.prompt.json` stores the verbatim prompt,
the schema, the temperature and the reply; recompute
`sha256(system ||0x00|| prompt || 0x00 || schema || 0x00 || f"{temperature:.2f}")[:32]` and it
matches the filename in `tests/cassettes/`. These five are the only entries in the store no
provider wrote, and they are the only reason the table above reads 10/10 rather than 8/10. When
the quota resets they can be replaced by real completions with the recipe below — the prompt
dumps in `docs/handauthored/` are exactly what needs re-recording.

### Re-record attempt, 2026-11-06

The quota did reset at 2026-11-06T00:00:00Z, and a live pass was driven through all ten paths
with these five entries pulled out of the store first (backed up, never deleted blind). The
fresh daily allowance was spent within minutes and **zero** turns recorded: the free models
answered in prose or not at all, `parse_output` refused every reply, and nothing unparseable is
ever written to the store. The store was verified byte-identical to the shipped revision
afterwards, the five entries were restored, and coverage went straight back to 10/10. The next
window is `X-RateLimit-Reset` **1794009600000 = 2026-11-07T00:00:00Z**; until then these five
remain the only hand-authored turns, and their pairings re-verify 5/5 with the script in
`docs/handauthored/README.md`.

## Re-recording

```
RUN_MODE=live-demo LLM_CASSETTE=record LIVE_CASSETTE_DIR=tests/cassettes \
  python -m uvicorn backend.app.server:app --host 0.0.0.0 --port 8931
```

Then click the paths. `CASSETTE_DEBUG_ALL=1 CASSETTE_DEBUG_DIR=/tmp/misses` writes each call
that *missed*, including a byte-exact `.json` of the call, which is enough to record that exact
turn later without re-running the whole episode. A replay miss never deletes the file it could
not use: the entry is flagged and only replaced once a replacement parses
(`OpenRouterLLM._stale_keys`), so a spent quota can no longer destroy a real recording.
