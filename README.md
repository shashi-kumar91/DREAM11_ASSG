# Bounded sports agents — one kernel, two live-match artifacts

A Dream11 FDE assignment prototype: one shared multi-agent kernel (`backend/kernel/`) with
two product packs bolted onto it, serving two different fan problems from the same
propose → verify → commit loop.

The rule the whole build follows: **the LLM proposes; deterministic code verifies, labels and
commits.** No model authors a number, a truth badge, or an event the fan has not reached.

## The two artifacts

### Gap Receipt (`backend/packs/gap/`, route `/gap`) — Pack B

**Fan problem.** You are watching a chase on a phone. An ad overlay, a 12-second buffer
stall, or opening the app late takes you out of the match. Coming back you have exactly one
question — *what did I miss?* — and two bad answers available today: a chatbot that guesses,
or a highlight reel that spoils the ball you just missed.

**What it does.** The boundary between "watched" and "missed" is estimated from the fan's own
player heartbeat log (`boundary.py`), under fixed signal rules — only balls the renderer
actually painted count as watched, an `ad_start` widens the boundary by a ball either side, a
`stall` keeps the stalled ball inside the gap. Where the candidate boundaries would produce
*different* receipts, the agent asks one specific question ("Did you see the wicket at ball
16.3 (142/4)?") and never asks when they would not. Then it commits one of: a receipt (score,
wickets, required-rate shift, scoreboard frames read out of the pixels), an explicit no-op
("2 balls passed, none of them a wicket or a boundary — nothing worth your attention"), or a
jump-to-live card. Nothing at or past the resume cursor can be released, in the agent's clamp,
in the commit gate, or on the media route.

Golden scenarios: `backend/fixtures/gap_scenarios.json` — S1 uncertain ad gap (asks,
receipt), S2 stall with no key action (does **not** ask, no-op), S3 late join (asks about
scope, whole clamped window).

### Watch Along (`backend/packs/creator/`, route `/creator`) — Pack A

**Fan problem.** The fan does not want to burn data and attention on a creator video to get
one score line; the creator wants to be heard. A live Hinglish stream is full of claims,
opinions, predictions and filler, and a text card that quietly promotes an opinion to a fact
destroys trust.

**What it does.** Each creator segment is transcribed (real Gemini audio route, or the cached
gold transcript with an honest badge), filtered by a Tier-0 triage that drops greetings and
restatements **without any model call**, then cross-checked against the match feed. A spoken
score is verified by code before a single model turn decides whether it matters
(`_prescreen`), so a contradiction cannot be skipped into silence. Cards land labelled
VERIFIED vs feed / CONFLICTS with feed / Opinion / Not checkable. Predictions are logged in a
ledger and settled against the feed when the over actually completes, with a numeric tally
that licenses the digits on the settlement card. A fan can ask "what did he say about the
death overs?" and get an attributed, timestamped excerpt — or an honest "no segment matches"
card.

### Agent room (`frontend/src/routes/AgentsRoute.tsx`, route `/agents`)

**Why it exists.** Both packs are the same kernel, and a jury cannot see a kernel from a card.
This page draws the run while it happens: the supervisor that chooses each step, the five
specialists it can wake, the tools each of them is allowed to touch, and the commit gate.

**What it refuses to fake.** The roster comes from `GET /api/kernel`, which introspects
`build_agents()` and the real tool registries — the chips on screen are the permission sets.
Every state change is a frame that arrived on the SSE stream (`supervisor_decision` →
*waiting*, `tool_call{caller}` → *using a tool*, `agent_result` → *reported*,
`budget_block{where:"agent:…"}` → *out of budget*). Because the supervisor takes one step at a
time inside an episode, this page never claims agents run in parallel inside one episode; the
"race" view instead starts **three real sessions at once** and shows three rooms side by side,
which is parallelism the server actually performs. A recorded episode finishes in milliseconds,
so an optional "read at human pace" reveals its frames one at a time and is labelled as pacing.

## Quickstart

Python 3.13 (this was run on 3.13.9 on Windows under Git Bash).

```bash
# 1. install (every pin in requirements.txt is the version this README's runs used:
#    fastapi 0.141.1, starlette 1.7.0, uvicorn 0.54.0, pydantic 2.12.4, httpx 0.28.1,
#    google-genai 2.8.0, numpy 2.3.5, pillow 12.0.0, pytest 8.4.2, python-dotenv 1.1.0)
python -m pip install -r requirements.txt

# 2. configuration — optional for everything below.
#    If .env does not already exist, copy .env.example; never fill a key into a tracked file.
cp .env.example .env        # skip if .env exists; it is gitignored and dockerignored

# 3. tests — 252 passed, 1 xfailed, 3.87 s, no network
python -m pytest tests -q

# 4. headless Pack B on a zero-call budget: 3 cards, 0 llm calls, badge llm: unavailable
python backend/scripts/run_pack_b.py --llm off

# 5. headless Pack B from the recorded cassettes: 3 cards, 22 llm calls, badge llm: cassette
python backend/scripts/run_pack_b.py --llm replay

# 6. headless Pack A, cached ASR, cassettes only: 8 cards, 58 llm calls, badge llm: cassette
python backend/scripts/run_pack_a.py --asr cached --llm replay

# 7. the HTTP server (also serves frontend/dist when it exists)
python -m uvicorn backend.app.server:app --host 127.0.0.1 --port 8931
curl -s localhost:8931/api/health
```

Steps 3–7 were each run on this checkout and the numbers in those comments are what the commands
printed. The frontend is `npm install && npm run build` inside `frontend/` and is served at `/`
from `frontend/dist` (built here: one hashed JS bundle and one CSS file under `dist/assets`, plus
the twelve `audio/uXX.wav` segments; `curl` on the running server returns that shell, referencing
the current bundle, and `/api/health` reports `frontend_built: true`). Until `dist` exists the
server answers `/` with a placeholder page (`backend/app/server.py`). The endpoints this README
exercises — `GET /api/health`, `POST /api/session`, the `GET /api/session/{sid}/events` SSE stream
and `POST /api/session/{sid}/answer` — answered as `docs/API_CONTRACT.md` describes, and that file
is the frozen agreement for all of them. What a terminal cannot certify is whether the shell
*looks* right on a real phone; the honest limits below say so.

## What a reviewer should see

Two things, measured separately.

**The loop works with no key and no network.** `python -m pytest tests -q` → 252 passed,
1 xfailed. `python -u evals/run_evals.py` → 4 runs, 11 cards, 36 model turns,
24 tool calls, 0 spoilers, 0 unlicensed, every row badged `cassette`,
`PASS - all four hard invariants hold`. `python -u evals/run_evals.py --llm off` → the same four
runs with **0 model turns and 0 tool calls** still committing 5 cards, badged `unavailable`. That
last one is the no-silence proof: with the model switched off entirely the kernel still ships an
artifact.

**The live path is real, and it is the model writing.** Measured on this checkout, against the
real providers:

* **Live text.** `LLM_CASSETTE_DIR=/tmp/live_cassettes LLM_CASSETTE=record python -u
  backend/scripts/run_pack_b.py --llm live --only S2 --budget 8` → PASS, 1 card, 8 live calls,
  `done` badge `llm=live`, ~80 s. The cassette directory was empty, so every one of those turns
  was a network call to `nvidia/nemotron-3.5-super-vl:free`.
* **Live ASR.** `GeminiASR` on the fixture audio, segment u02 → `"yeh banda ab reverse sweep try
  kar sakta hai field dekho, third man up hai"` (verbatim against the gold Hinglish line),
  confidence 1.0, which then ran a real episode. Segment u04 → `"Vikram Naidu out ho gaye. Score
  abhi 142/4 hai."`, confidence 1.0, card committed.
* **Live ASR on noisy audio.** Segment u03 comes back **empty**. Before this revision that empty
  string was ingested at confidence 1.0 and handed to the supervisor; now it is gated
  (`reason: asr_empty_transcript`), the segment is added to `not_evidence`, no episode runs and
  no card is invented. `tests/test_audit_fixes.py` pins the rule with a stub that mimics the
  provider exactly.
* **The gate, on live model output.** `run_pack_a.py --asr live --llm live --only u02,u03`:
  real Gemini ASR, real OpenRouter turns, badges `{asr: live, llm: live}`. On u02 the model wrote
  a card containing the token `02` that was in no cited evidence; `commit` refused it —
  `rule: unlicensed_number, offending: '02'` — so **nothing was published**, and the episode then
  hit its call budget (`terminal: exhausted`). Silence from a gate that will not accept a
  model-authored digit is the product working. On u03 the same run printed
  `ASR ABSTAIN u03: conf=0.00 transcript=''` with `reason: asr_empty_transcript`.
* **A live web session.** `RUN_MODE=live-demo` against an empty cassette dir, gap S1 over HTTP:
  the kernel asked its boundary question, dispatched the investigator, hit its own budget cap
  (`episode_end terminal=exhausted`), and the deterministic fallback still committed the receipt.
  Wall clock ~4 minutes for 10 live calls: free-tier reasoning models are slow, which is exactly
  why the deployed default is `RUN_MODE=cached-demo` + `LLM_CASSETTE=replay`.
* **A dead model, recorded as dead.** `stealth/crouton-alpha` — the model string this project
  shipped with — returns `content: null` and `completion_tokens: 0` even at 300 max_tokens. It is
  no longer in `.env`. `OpenRouterLLM` now remembers a route that just proved dead
  (`OPENROUTER_DEAD_COOLDOWN_S`, default 600 s) instead of paying for its empty completion on
  every turn of an episode.

The offline equivalence claim is unchanged: the same scenarios replayed from the checked-in
cassettes return the same cards with `llm: cassette`, and `compare_runs.py` prints MATCH for
record-vs-replay.

To run a real model path you need a key in `.env` and to opt in explicitly:

```bash
python backend/scripts/run_pack_b.py --llm record     # live on store misses, records what it fetches
python backend/scripts/run_pack_a.py --asr live --llm live
python backend/scripts/drive_http.py --base http://127.0.0.1:8931 --scenario S1 --pack gap
RUN_MODE=live-demo python -m uvicorn backend.app.server:app --port 8931
```

Two black-box probes run against a live server and exit non-zero on the first broken promise:
`python backend/scripts/probe_http.py` → **33/33** (status codes, SSE `Last-Event-ID` resume, clip
refusals in quiet mode, frame bytes, `/api/facts`, `/api/kernel`, four sessions at once) and
`python backend/scripts/probe_dynamic.py` → **18/18** (a heartbeat the fixture never recorded, a
heartbeat after the resume cursor, a vague answer that names no ball, an answer that arrives after
the window closed, a creator query nothing in the transcript answers, six abandoned sessions, then
a fresh one that still runs).

`drive_http.py` is the one that proves the product over the wire rather than in-process: it
POSTs a session to a running server, streams the real SSE journal, answers the agent's question
as the fan, and exits non-zero if the run ends with no card, no `done` frame, or the fan being
asked twice. Measured on this checkout: gap S2 against a `live-demo` server whose cassette
directory was empty → 1 card, 24 SSE frames, `llm_mode: live`, badge `llm: live`, 19 trace
events, 0 bytes, PASS. Gap S2 against an `offline-ci` server on the checked-in cassettes → the
same card, 27 SSE frames, `llm_mode: cassette`, 22 trace events, 0 bytes; gap S1 there asks
"Did you see the wicket at ball 16.3 (142/4) before you left?", takes the fan's answer and
commits the receipt. Same card and same call count, different badge — which is the point. The
stronger equivalence claim, record then replay, is the `compare_runs.py` MATCH line in the
no-key section below.

## The four run modes

`RUN_MODE` (per-session override via `run_mode` in `POST /api/session` is also accepted).

| mode | text backend | ASR | what it is for |
|---|---|---|---|
| `offline-ci` | cassette replay, never live | cached | CI and reviewers with no key; also defaults `auto_answer` on, so fixture fan answers resolve blocking questions |
| `cached-demo` | cassette replay, never live | cached | the default; stage-proof demo of the whole loop |
| `live-demo` | live provider on any turn the store does not already have, **records** the rest | live unless `ASR=cached` | the only mode that can make a network call for text or audio |
| `safe-demo` | cassette replay, never live | cached | intended as "cassette first, live only to fill a miss" |

**Read that last row honestly.** `backend/app/server.py` (`make_llm`) sets
`live = mode == "live-demo"`, so today `safe-demo` is behaviourally identical to `cached-demo` —
both build `OpenRouterLLM(live=False, cassette_mode="replay")` and `CachedASR`. The "cassette
first, live to fill a miss" rung only exists in the docstring of `backend/kernel/factory.py`,
which nothing imports (grep for `kernel.factory` returns no callers). The live-vs-record split is
a real code path; the refill-on-miss path is not wired. That is stated here rather than hidden in
a doc that is not in the repo.

## Running with no API key at all

Three layers, and they are independent:

1. **Deterministic core.** Boundary estimation, required-rate arithmetic, value-of-information,
   scoreboard OCR, the spoiler clamp, the verifier, the ledger and `commit.py` are pure code
   over fixtures. No key, no network.
2. **Cassettes** (`tests/cassettes/`, 585 content-addressed JSON recordings) replay recorded
   model turns by hash of `(system, prompt, schema, temperature)` — the model name is excluded,
   so a turn recorded from OpenRouter replays under Gemini and vice versa.
3. **Deterministic fallback.** When no model turn survives, the kernel still commits a card
   built entirely from code templates.

What those layers produce on this checkout, with no key — every number below printed by the
command next to it:

- `run_pack_b.py --llm replay` → all three golden scenarios **PASS**, 3 cards, `llm calls: 22`,
  43 trace events, badge `llm: cassette`. The cassettes hit.
- `run_pack_b.py --llm off` → same three scenarios **PASS**, 3 cards, **`llm calls: 0`**, badge
  `llm: unavailable`, 19 trace events. The script hands the kernel a zero-call budget, so the
  kernel walls at step one and the cards on screen are the deterministic fallback's: S1 and S3
  are full receipts (S3: `135/3 -> 149/5`, required rate `8.48 -> 11.57 (+3.09)`,
  `27 needed off 14 balls`), S2 is the honest no-op. No model, no silence.
- `run_pack_a.py --asr cached --llm replay` → **8 cards**, `llm calls: 58`, 107 trace events,
  badges `{"asr": "cached", "llm": "cassette", "feed": "replay_fixture", "telemetry":
  "simulated"}`. The trace shows the agent path, not a template dump: 10 episodes,
  **23 supervisor decisions**, **18 tool calls**, 9 commits of which
  **1 was rejected by the gate**, 2 segments dropped by Tier-0 triage with no model call at all,
  1 ASR abstention, and 1 prediction settled against the feed.
- `evals/run_evals.py` → 4 runs, 11 cards, 36 model turns, 24 tool calls, 0 spoilers,
  0 unlicensed, badge `cassette` on every row, `PASS`.
- `evals/run_evals.py --llm off` → the same 4 runs at **0 model turns, 0 tool calls**, still
  5 cards, badge `unavailable` on every row, `PASS`.
- `compare_runs.py openrouter record replay` → `MATCH: 3 identical cards, 14 model turns,
  15 tool calls, 0 fallback commits`.

**The uncomfortable part, restated honestly:** it used to be that the checked-in cassettes missed
the supervisor turn of both packs and every offline run was a fallback dump. That is no longer
what the commands print — the store now hits, and `--llm replay` runs the real agent path offline.
What remains true is that the two proofs are different proofs: replay shows the *recorded* agent
path is reproducible without a key; `--llm off` shows the artifact still ships when there is no
model at all. Neither is a substitute for the live path, which is why the live measurements are
above rather than a promise.

The HTTP layer proves the same thing without a UI: with `RUN_MODE=offline-ci`,
`POST /api/session {"pack":"gap","scenario":"S2","mode":"watch"}` streams the no-op card through
27 SSE frames with 14 cassette-backed calls, 22 trace events and `total_bytes: 0`, and the `done`
frame carries the badges.

## What is real vs simulated

Read from each session's own `badges()` method — the same dict the UI is not allowed to
hand-edit — plus the fixture builder. Nothing here claims production integration.

| Surface | State | Where the truth lives |
|---|---|---|
| Match feed (18 events, labels 15.1-17.6 — 12 ball, 2 boundary, 2 wicket, 2 interval — 10-16 s apart, squads, 176 target) | **replay fixture**, fictional teams | `backend/fixtures/match_feed.json`, `match_meta.json` |
| Player telemetry / heartbeats | **simulated** | `backend/fixtures/gap_scenarios.json` (note field says so) |
| Scoreboard frames | **rendered fixtures**, one injected fault (`score_bad_143_4.png`) | `backend/fixtures/build_fixtures.py`, `frames.json` |
| Scoreboard reading | **real pixel OCR** (numpy + Pillow template match, not a lookup) | `backend/packs/gap/ocr.py` |
| Creator Hinglish audio | **TTS-synthesized** (`edge-tts` hi-IN voice, `ffmpeg` resampled, synthetic noise bed on the "noisy" segments) | `backend/fixtures/build_fixtures.py` |
| ASR | **real** Gemini audio route when `--asr live` / `RUN_MODE=live-demo`; otherwise the gold transcript, badged `asr: cached` | `backend/adapters/asr.py` |
| Video clips | **no encoded video exists.** The clip is a priced, byte-exact stand-in: EBML magic + ball metadata + hash-chained body | `backend/app/server.py` (`_clip_chunks`) |
| LLM reasoning | **real** HTTP calls to OpenRouter / Gemini in `live-demo` and `--llm live/record` — but a turn already in the store is replayed from it even in those modes (`openrouter.py: complete()` loads the cassette before the provider), which is why the live badge above needed an empty `LLM_CASSETTE_DIR`. Cassette replay or deterministic fallback everywhere else, always badged `llm: live/cassette/unavailable` | `backend/kernel/openrouter.py`, `llm.py` |
| Agent loop, guardrails, verifier, ledger, commit gate, byte metering, SSE journal | **real code, running** | `backend/kernel/`, `backend/metering/bytes.py` |
| Dream11 / FanCode integration, auth, payments, real ad stack, real device telemetry | **absent by design** | — |

## Honest limits

- 18 feed events over three overs (15.1-17.6), one 186-second chase window, 12 creator segments,
  5 scoreboard frames, 3 gap scenarios. This is a proof of the loop, not a scale claim.
- The offline replay path runs the real agent loop today (26 supervisor decisions and 21 tool
  calls across 10 episodes in the Pack A trace), but what the *model* chose to write is only as
  current as the recording. A live pass re-run today committed 6 Pack A cards before dying on a
  90-character headline cap in `creator/templates.py:74`; the gap pack's live path completed.
- Nobody has eyeballed a screenshot of the built player on a real phone. The frontend builds, the
  server serves the shell, and every card, badge and byte figure quoted in this README came from a
  command's own output — a pack script's summary line or the SSE `done` frame — never from the UI.
  The visual layer itself is unverified.
- `frontend/dist` is build output, not source — `npm run build` regenerates it. The bundle hash
  changed once while this README was being written, so no hashed filename is quoted here.
- The cassette store is appended to by every recording pass (`tests/cassettes/` went from 440 to
  454 entries during the day), so the count in this README is a measurement, not a constant.
- Pack B has no per-fan learned priors: boundary confidence is fixed signal rules
  (0.95 clean, −0.30 ad, −0.05 stall, −0.20 no-resume), not a calibrated model.
- The value-of-information test compares *key-event sets*, so a boundary that only changes the
  filler balls will not ask. That is deliberate and it is a limitation.
- Required rate is computed over a fixed 20-over, 120-ball assumption
  (`rates.py`), which is why the recomputed rate agrees with the fixture feed.
- OCR reads only the three bands it renders (`score`, `over`, `rrr`), at its own font and
  geometry. It is a real pixel reader with a synthetic scoreboard, not a general scoreboard
  detector.
- ASR confidence is the model's self-reported certainty, not an acoustic score — `asr.py`'s own
  docstring says Gemini does not emit one. It gates abstention
  (`creator/session.py`: `res.confidence < cfg.asr_conf_floor`), and the player shows the number
  plus an "abstained below the ASR confidence floor (nothing invented)" notice
  (`frontend/src/components/creator.tsx`).
- The clip is a byte-shaped stand-in, so the bytes graph measures the *policy* honestly and the
  codec cost not at all.
- Budgets: `run_pack_a.py` defaults to `--budget 12` LLM calls per episode, `run_pack_b.py` to 14,
  both with 12 tool calls, 240 s and 8 specialist rounds as the other walls (`_budget` in each
  script). A budget stop produces a card, not silence: the Pack A replay run above printed
  `deterministic fallback after budget:max_llm_calls` and the HTTP traces carry `budget_block`
  events. One supervisor per episode, making a sequence of decisions — the Pack A replay run
  logged 26 of them across its 10 episodes.
- No auth, no persistence, no multi-tenant concerns: sessions live in process memory
  (`MAX_SESSIONS`, default 12) and a restart forgets them.

## Layout

```
backend/kernel/     orchestrator (supervisor + capability-scoped specialists), commit gate,
                    clamp, registry, schemas, trace, Gemini + OpenRouter transports
backend/packs/gap/  boundary, voi, ocr, rates, receipt templates, guardrails, agents, tools
backend/packs/creator/  triage, verify, predictions ledger, policy, templates, agents, tools
backend/adapters/   ASR (live Gemini + cached)
backend/app/        FastAPI + SSE + per-session journal + byte metering + static frontend
backend/metering/   ByteMeter: server-side, cumulative per session
backend/fixtures/   replay feed, gap scenarios, scoreboard frames, audio + builder
backend/scripts/    run_pack_a/b, drive_http, probe_http (contract), probe_dynamic (edge cases)
tests/              253 cases (252 pass, 1 xfail) + the cassette store (585 recordings)
                    see docs/CASSETTES.md for what those recordings cover
docs/               SPEC.md (the per-functionality backend↔UI contract), screenshots/, 
                    ATTRIBUTION.md (every photo, its author, licence and source file),
                    API_CONTRACT.md (the frozen frontend/backend agreement),
                    TECHNICAL_DOCUMENT.md (+ .pdf, .html) — architecture, method, decisions,
                    SUBMISSION_NOTE.md (the ≤200-word note and the two links)
frontend/           fan pages + agent-room animation + jury pane (React/Vite/TypeScript)
DEPLOY.md           how to host it with no key and no build step
```

Deeper reading: `docs/TECHNICAL_DOCUMENT.md` is the long form — architecture, the propose/verify/
commit rule, the agent mechanics, both packs' evidence chains, the cassette method, the HTTP and
byte surface, the evaluation and the limits, with the decisions that shaped them.
`docs/API_CONTRACT.md` is the wire format (read-only — it is the frontend's contract).
`DEPLOY.md` is the hosting path. `model_ideas.txt` is the original spec this build was cut
against.
