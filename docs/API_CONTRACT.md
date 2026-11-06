# HTTP contract (the only place frontend and backend agree)

Single origin in production: FastAPI serves the built React app at `/` and the API under `/api`.
No auth anywhere — a reviewer opens the link and it works.

## Runtime modes (`RUN_MODE`, per-session override allowed)

| mode        | llm              | asr     | clips                        |
|-------------|------------------|---------|------------------------------|
| `offline-ci`| cassette replay  | cached  | never fetched                |
| `cached-demo`| cassette replay | cached  | served from fixture bytes    |
| `live-demo` | live provider    | live    | served from fixture bytes    |
| `safe-demo` | live, abstain-on-miss | cached | served from fixture bytes |

## Sessions

`POST /api/session` `{"pack": "gap" | "creator", "scenario": "S1", "mode": "watch" | "quiet"}`
→ `200 {"session_id": "s_...", "pack": ..., "scenario": ...}`.
The run starts on a worker thread; every kernel event is appended to a session journal so a
late subscriber can catch up. One session = one fan = one episode set.

`GET /api/session/{sid}/events` → `text/event-stream`. Replays the journal from the beginning,
then follows live until `done`. Each frame is `event: <type>\ndata: <json>\n\n` with types:

- `episode_start` `{"episode_id", "trigger", "objective"}`
- `episode_context` `{"episode_id", "observation"}` (the clamped observation an agent was given)
- `boundary` `{"candidates": [...], "confidence", "notes": [...]}`
- `ask_user` `{"episode_id", "question": {"text", "options", "reason"}, "blocking": true}`
- `answer` `{"question", "answer"}`
- `agent_result` `{"agent", "tool"|"found", "detail"}`  / `agent_no_final`
- `supervisor_decision` `{"step", "action", "agent"|"tool", "thought", "remaining"}`
- `tool_call` `{"tool", "args", "ok", "preview", "ms", "caller", "blocked"}`
- `guardrail` `{"rule", "why", "offending"}`
- `modality_overridden` / `terminal_vetoed` / `clip_rejected` / `no_question` / `uncertainty`
- `card` `{"card": Card.model_dump(), "badges": {...}}` — `Card` already carries
  `truth_label`, `template_used`, `numbers`, `cited_event_ids`, `cited_tally_ids`
- `commit` `{"ok", "why", "rules"}` (a rejected commit is visible, not swallowed)
- `bytes` `{"endpoint", "bytes_sent", "kb", "reason"}` — every clip byte is counted here
- `status` `{"note"}` (fallbacks, budget walls)
- `done` `{"cards", "llm_calls", "trace_events", "badges"}`
- `error` `{"where", "message"}`

`POST /api/session/{sid}/answer` `{"text": "..."}` → `200 {"ok": true}`; resolves a blocking
`ask_user`. `POST /api/session/{sid}/answer` `{"option_index": 2}` is accepted too.

When no question is pending the answer is refused with
`409 {"error": "no question is pending for this session", "hint": "questions arrive as
`ask_user` frames on the events stream; poll /api/session/{sid} for pending_question"}`.
A 200 in that situation would tell a client its answer was heard by a run that never asked.

`POST /api/session/{sid}/interrupt` `{"kind": "ad_start"|"ad_end"|"stall"|"resume"|"gap"|"join"|"play"}`
→ `200 {"ok": true, "seq": n}`. Appends player heartbeat to the session's telemetry, which the
boundary estimator reads. This is how a jury proves the boundary tracks the player, not the feed.

`GET /api/session/{sid}/trace` → `{"events": [...], "badges": {...}, "cards": [...], "budget_used": {...}}`

`GET /api/facts` → every figure the interface is allowed to print, read from the fixtures with no
session and no provider call: `{match, state, feed[], frames[], gap_scenarios[], creator, provenance[],
run, photo_credits[]}`. `frames[]` carries both `drawn_score` (what the rendered frame shows) and
`feed_score` (what the fixture says), so the UI can compare them instead of printing one as the other.

### Cases nobody scripted

`POST /api/session` also accepts a situation that is not in the fixtures:

```json
{"pack": "gap", "scenario": "custom", "mode": "watch",
 "resume_event_id": "evt_072", "interruption": "ad" | "stall" | "app" | "late"}
{"pack": "creator", "scenario": "custom", "text": "Aarav Sen agle over mein ek six maar dega"}
```

| field | meaning |
|-------|---------|
| `resume_event_id` | any id the feed actually holds; an unknown one returns `400` with `known_balls` |
| `interruption` | what happened to the fan; the boundary is re-estimated from synthesised heartbeats |
| `text` | typed words stand in for ASR; the badge reports `asr: typed`, never transcribed |
| `llm` | optional per-session layer: `"live"` \| `"replay"` \| `"off"` |

A custom case has no recording, so when a provider key is configured it runs on the live model
pool and the response says so (`llm_layer`, `llm_note`). Every card still passes the same commit
gate: a number the feed does not contain is refused, whatever wrote it.

`GET /api/models` → what the pool is, without spending a request:

```json
{"ok": true, "members": ["gemini:gemini-3.1-flash-lite", "openrouter:nvidia/...:free"],
 "rotate": true, "cooldown_s": 240, "layers": ["replay", "live", "off"]}
```

`GET /api/kernel` → the introspected roster the agent-room animation draws:
`{supervisor{role,mission,contract[],cannot[]}, packs{gap{agents[],tools[],supervisor_capabilities,
critic_required_before_commit}, creator{...}}, walls[{id,where,claim}]}`. Built from
`build_agents()` and the real `ToolRegistry` (`backend/app/kernel_map.py`); the toolboxes are
constructed with `session=None` and only read, so it cannot touch a feed or an LLM.

`GET /api/manifest` → `{"run_mode", "backend", "packs": [{"id", "route", "title", "subtitle",
"scenarios": [{"id", "name", "blurb", "expected"}]}]}`

`GET /api/health` → `{"ok": true, "run_mode", "llm_backend", "cassettes": n}`

`GET /api/media/clip/{event_id}` → `video/x-matroska` (or the fixture container), streamed through
a byte counter that emits a `bytes` event. A `quiet` session must never be charged: the endpoint
refuses with `403 {"why": "quiet mode never loads clip bytes"}` unless the session asked. The
counter, not the client, is the source of truth for the bytes graph.

## Frontend

Two routes, both mobile-first, plus a jury pane that can be toggled on either:

- `/creator` — Watch Along: creator card feed, prediction card + settlement, transcript search,
  interruption buttons, audio.
- `/gap` — Gap Receipt: player position strip, the ask-user card with the option buttons, the
  receipt card (headline/body/truth badge), spoiler indicator, clip tile that shows the byte
  counter only after a tap.
- Jury pane (right/bottom drawer): live trace list, budget meter (`llm_calls` / `tool_calls` /
  rounds), bytes graph (cumulative KB vs "would have cost" if every clip had loaded), spoiler
  indicator (green = nothing past the cursor was ever released), and a `run mode` badge.

`Watch` vs `Quiet` is a real toggle that changes server behaviour (`mode` in `POST /api/session`),
not a CSS class.
