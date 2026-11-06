# SPEC — the backend↔UI contract, per functionality

This is the document the UI is built against and the server is tested against. One row per
user-visible functionality: what the fan sees, which endpoint or frame feeds it, what the
server guarantees, and which test would fail if the guarantee broke.

Rules that hold for **every** row:

1. The UI prints no number it did not receive. There is no hardcoded score, over, boundary,
   percentage or card count anywhere in `frontend/src` — the only literals in a rendered card
   come from the card the server committed.
2. Every frame is published by the server before the UI can draw it (`backend/app/server.py`
   journals each frame and replays it to late subscribers), so a reload shows what happened,
   not a blank page.
3. When the server has nothing to say, it says nothing: an empty state in the UI is a real
   absence, not a placeholder pretending to be data.

---

## 1. Page-load facts (no session, no model call)

| | |
|---|---|
| UI | Home page match card, ball strip, scenario cards, frame gallery, "real vs simulated" table, photo credits, footer counts |
| Endpoint | `GET /api/facts` |
| Guarantee | Read-only, session-free, makes no provider call. Values come from `backend/fixtures/*` and the recorded traces on disk. `match.fictional` is `true` and the UI prints it. |
| Tests | `tests/test_facts_endpoint.py` (9 tests) — every score, over and frame read in the payload is checked against the fixture files |

## 2. Kernel roster (no session, no model call)

| | |
|---|---|
| UI | Agent-room page (`/agents`): the roster wall, the tool/capability chips, the "what the code will not let them do" list |
| Endpoint | `GET /api/kernel` (`backend/app/kernel_map.py`) |
| Guarantee | Introspected from `build_agents()` and the real `ToolRegistry`; the toolboxes are constructed with `session=None` and only read, so this endpoint cannot touch a feed or an LLM. Roles, missions, tool lists and capability sets are the kernel's own objects. |
| Tests | `tests/test_audit_fixes.py::test_kernel_map_is_introspected_not_hand_written` |

## 2b. Cases nobody scripted

| | |
|---|---|
| UI | "Tell it your own situation" (gap) / "Put words in the creator's mouth" (creator) |
| Endpoint | `POST /api/session` with `scenario: "custom"` (`backend/app/custom.py`) |
| Guarantee | The situation is built from the request — any ball the feed holds, any interruption, any typed creator line — and handed to the same kernel, clamp and commit gate as the recorded ones. An unknown ball is refused by name (`400` + `known_balls`); typed text is badged `asr: typed`, never as a transcription. The response carries `llm_layer` and `llm_note`, so the page can say which transport actually answered. |
| Tests | `tests/test_custom_cases.py` (every ball × every interruption, the late-join case, the typed-ASR case), probes 30–33 |

## 2c. Which models answer

| | |
|---|---|
| UI | the "where it came from" panel in "Show the working" |
| Endpoint | `GET /api/models` (`backend/kernel/router.py`, `backend/kernel/factory.py`) |
| Guarantee | The pool is built from the keys that exist: one member per model string, round-robin per call, per-member cooldown after a failure. The router reports the model that actually served the turn; when nothing serves, the episode says so and the kernel finishes in code rather than inventing words. Layers: `replay` (recordings only), `live` (pool), `off` (no transport). |
| Tests | `tests/test_custom_cases.py` (rotation, cooldown, served-model reporting, all-down raises) |

## 3. Start a run

| | |
|---|---|
| UI | "Run scenario" / "Run one episode" / "Race all three situations" |
| Endpoint | `POST /api/session` `{pack, scenario, mode, run_mode?, auto_answer?, query?}` |
| Guarantee | Unknown pack / scenario / mode → `400` with the known list. A run starts on a worker thread and the response returns immediately. Several sessions may run at once (the race view uses this). |
| Tests | `tests/test_acceptance_gap.py`, `tests/test_acceptance_creator_http.py`, API probe cases 1–4 |

## 4. Live kernel stream (the whole point)

| | |
|---|---|
| UI | Every live element on all three artifact pages, and the agent-room animation |
| Endpoint | `GET /api/session/{sid}/events` (`text/event-stream`) |
| Guarantee | Replays the journal from the beginning, then follows until `done`. `Last-Event-ID` is honoured, so a dropped connection resumes without gaps or duplicates. Frames are flat JSON objects whose fields the UI reads directly (`frames.ts` maps each type to its printed fields). |
| Frames | `episode_start`, `episode_context`, `boundary`, `supervisor_decision{action,agent,tool,reason,thought}`, `tool_call{tool,args,ok,preview,ms,caller}`, `agent_result{agent,result}`, `agent_no_final`, `agent_schema_retry`, `commit{ok,card_id}`, `commit_rejected`, `guardrail{rule,why}`, `ask_user`, `answer`, `auto_answer`, `asr_abstain`, `triage`, `episode_skipped_tier0`, `kernel_verifier`, `prediction`, `prediction_settled`, `surface`, `bytes`, `budget_block`, `modality_overridden`, `terminal_vetoed`, `clip_rejected`, `interrupt`, `card`, `status`, `error`, `done` |
| Tests | `tests/test_acceptance*.py` consume these frames; API probe case 19 asserts `Last-Event-ID` resume |

## 5. The subagent animation (agent room)

| | |
|---|---|
| UI | `/agents`; a supervisor card, one card per specialist, a bus, a fan card, a commit-gate card and a kernel log |
| Source | The same SSE frames as §4 — no second channel, no polling |
| Derivation | `frontend/src/lib/room.ts`. `idle` = never dispatched; `working` = a `supervisor_decision{action:"dispatch", agent}` arrived; `using a tool` = a `tool_call{caller}` arrived; `reported` = `agent_result`; `out of budget` = `budget_block{where:"agent:<role>"}`; `errored` = `agent_error` |
| Guarantee | The animation cannot show work that did not happen: every transition is a frame. Two honest limitations are stated on the page — the supervisor takes **one** step at a time inside an episode, so per-episode parallelism is never claimed; the race view shows **several episodes** running at once, which the server really does. A recorded episode finishes in milliseconds, so the optional "read at human pace" reveals frames one at a time and is labelled as pacing, not as live activity. |
| Tests | `tests/test_audit_fixes.py::test_kernel_map_...` pins the roster the animation may draw |

## 6. The boundary question (Gap Receipt)

| | |
|---|---|
| UI | Ask card with the question and its options |
| Frames | `ask_user{question:{text,options,reason}}` → fan clicks → `POST /api/session/{sid}/answer {text \| option_index}` → `answer_accepted`, `answer` |
| Guarantee | The value-of-information test is code (`backend/packs/gap/voi.py`): the fan is asked only when candidate boundaries would produce different receipts. A second answer when nothing is pending is `409`, not a silent success. An answer that names no candidate ball moves the preference but never the window. |
| Tests | `tests/test_audit_fixes.py::test_answer_without_a_pending_question_is_a_conflict`, API probe cases 6 and 9 |

## 7. The committed card

| | |
|---|---|
| UI | Receipt / no-op card, verified-conflict cards, predictions |
| Frames | `commit{ok,card_id}` + `card{card,badges}` |
| Guarantee | A card is committed only by `backend/kernel/commit.py`, which re-derives the truth label from the evidence and refuses any digit absent from cited evidence. A refused commit emits `commit_rejected` / `guardrail` and commits nothing. The badges on the card say where each input came from (`llm=cassette\|live\|unavailable`, `asr=cached\|live\|n/a`, `feed=replay_fixture`, `frames=rendered_fixture`). |
| Tests | `tests/test_session_commit.py`, `tests/test_kernel.py`, evals' "unlicensed digits" invariant |

## 8. Spoiler clamp

| | |
|---|---|
| UI | Spoiler-check panel, ball strip with the gap shaded, "nothing after your cursor" copy |
| Sources | `GET /api/session/{sid}` (`released_events`), `boundary` frame, `clip_rejected` / `terminal_vetoed` frames |
| Guarantee | Three independent walls: the clamped feed hands an agent no text for a ball at or after the resume cursor; `validate_receipt` refuses a citation past the cursor; `GET /api/media/clip/{id}` returns `403` for such a ball. |
| Tests | API probe cases 16–18, `tests/test_gap_pack_b.py`, eval invariant "spoilers 0" |

## 9. Byte meter and the clip

| | |
|---|---|
| UI | "Your data" panel, clip offer, clip load |
| Frames / endpoints | `bytes{endpoint,kb,total_kb,would_have_kb,reason}`; `GET /api/media/clip/{event_id}?sid=...`; `tool_call{tool:"offer_clip"}` for the quote |
| Guarantee | Every byte the client receives is counted by the server (`backend/metering/bytes.py`); the UI's number is the server's number. Quiet mode never serves clip bytes (`403`). |
| Tests | API probe cases 16–17, `tests/test_acceptance_gap.py` |

## 10. Scoreboard frames read out of pixels

| | |
|---|---|
| UI | Frame gallery, per-ball "read vs feed" comparison, mismatch highlighting |
| Endpoint | `GET /api/media/frame/{frame_id}` (image bytes), fields from `/api/facts` (`frames[]`) and `inspect_frame` tool results |
| Guarantee | `drawn_score` is what the rendered frame shows, `feed_score` is what the fixture feed says, and the UI compares the two instead of printing one as the other. A frame rendered with an injected fault is labelled `injected_fault` and shown as a disagreement. |
| Tests | `tests/test_facts_endpoint.py` (per-frame read/feed assertions) |

## 11. Creator: transcript, claims, predictions

| | |
|---|---|
| UI | Segment list with Tier-0 skips, claim cards, ASR abstentions, prediction ledger |
| Frames | `utterance`, `triage`, `tier0_skip`, `asr_abstain`/`abstain`, `kernel_verifier`, `prediction`, `prediction_settled`, `surface` |
| Guarantee | A blank transcript is not evidence even at confidence 1.0 (`asr_empty_transcript`), a low-confidence transcript abstains, and Tier-0 skips cost no model call. Predictions settle against the feed only, and the card says whether it hit or missed. |
| Tests | `tests/test_triage.py`, `tests/test_audit_fixes.py` (4 blank-ASR/label tests), `tests/test_conflict_is_never_skipped.py` |
| Label honesty | A `query_answer` card built from real creator quotes is labelled *Answer from creator memory*; the abstain variant — the creator never covered the question — is labelled *No evidence — nothing invented*, because a reader who only sees the badge must not believe an answer exists (`tests/test_audit_fixes.py::test_query_abstain_gets_its_own_truth_label`) |

## 12. Budget and failure honesty

| | |
|---|---|
| UI | "Under the hood" drawer, `done` frame, error lines |
| Frames | `budget_block{where,detail}`, `error{where,message}`, `done{cards,llm_calls,llm_mode,trace_events,badges,bytes,budget,errors}` |
| Guarantee | Hitting a cap is reported, never hidden. A provider outage downgrades the badge to `llm=unavailable` and the deterministic fallback still produces a receipt built from the clamped feed — the fallback says so on the card (`status` frame: "deterministic receipt after …"). |
| Tests | `tests/test_kernel.py` (budget walls), pack-B live run that ended `exhausted` and still committed |

---

## How this contract is verified

| Level | Command | What it proves |
|---|---|---|
| Unit + in-process HTTP | `python -m pytest tests -q` (253 cases) | the logic, the gate, the clamp, the pack behaviour, the routes |
| Black-box HTTP | `python backend/scripts/probe_http.py` (33 cases) | the contract as a client sees it on a running server: status codes, SSE resume, media refusals, `/api/facts`, `/api/kernel` |
| Dynamic cases | `python backend/scripts/probe_dynamic.py` (18 cases) | the states no golden fixture scripts: unrecorded heartbeats, heartbeats after the cursor, vague answers, late answers, unanswerable queries, abandoned sessions |
| Invariants | `python -u evals/run_evals.py` | 0 spoilers, 0 unlicensed digits, 0 silent runs across all four scenarios |
| UI | Playwright passes over `/`, `/gap`, `/creator`, `/agents` at 1440 px and 414 px | zero console errors, and each page rendered from server data |

---

## Degraded modes, stated up front

| Situation | What the user sees |
|---|---|
| No provider key / offline | `llm=cassette` (replayed recordings) or `llm=unavailable` with the deterministic receipt; never a fake "live" badge |
| Provider returns empty text | Fast-skip of that model, then the chain rotates; if the whole chain is dead the endpoint is marked `unavailable` |
| ASR returns empty text | The segment abstains (`asr_empty_transcript`); no card is invented for it |
| Session unknown | `404`; nothing is created implicitly |
| Answer with no question | `409` with a hint pointing at the events stream |
| Stream dropped | The client retries with `Last-Event-ID`; the journal guarantees no duplicated frame |
