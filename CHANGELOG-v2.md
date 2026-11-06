# CHANGELOG — revision 2 (2026-11-05)

What changed in this revision, why, and what was measured rather than assumed. Every number
below was read off a run of this checkout today.

## 1. Audit findings that were fixed

| # | Defect | Fix | Where | Proof |
|---|---|---|---|---|
| 1 | `POST /api/session/{sid}/answer` returned `200` even when no question was pending, telling a client its answer was heard by a run that never asked | `409` with the reason and a hint pointing at the events stream | `backend/app/server.py` | `tests/test_audit_fixes.py::test_answer_without_a_pending_question_is_a_conflict`, probe case 10 |
| 2 | An empty/whitespace ASR transcript was ingested at confidence 1.0 and handed to the supervisor — a live Gemini run really did return `""` with conf 1.0 | empty text is now gated as `asr_empty_transcript`, the segment joins `not_evidence`, no episode runs, no card is invented. Surfaced a second latent bug: `Uncertainty.kind` had no `asr_empty` literal, so the fix crashed before the `Literal` was extended | `backend/packs/creator/session.py`, `backend/kernel/schemas.py` | 3 tests in `tests/test_audit_fixes.py`; live rerun printed `ASR ABSTAIN u03: conf=0.00 transcript=''` |
| 3 | `OPENROUTER_MODEL=stealth/crouton-alpha` — a model that returns `content: null`, `completion_tokens: 0` at every budget — was the configured primary, so every turn paid for two dead attempts plus backoff | primary is now `nvidia/nemotron-3.5-super-vl:free`, the dead route is out of the chain, and a route that just proved dead (empty completion / 404 / 429 / timeout) is skipped for `OPENROUTER_DEAD_COOLDOWN_S` (default 600 s) instead of being retried on every turn | `backend/kernel/openrouter.py`, `.env`, `.env.example` | `_mark_dead` + `route_status()`; live pack B run completed 8 live calls |
| 4 | The subagent animation the brief asked for did not exist | New `/agents` page: supervisor, five specialists, tool/capability chips, fan card, commit gate and a live kernel log, all driven by the frames the server publishes. Race view runs three real sessions at once | `frontend/src/{lib/room.ts,lib/useLanes.ts,lib/kernel.ts,useKernel.ts,components/agents.tsx,routes/AgentsRoute.tsx}` + `backend/app/kernel_map.py`, `GET /api/kernel` | Playwright: 4 pages × 2 widths, zero console errors; screenshots in this archive |
| 5 | `query_answer` cards that had no evidence still carried the label *Answer from creator memory* | new `TruthLabel.NO_EVIDENCE` = *No evidence — nothing invented*, used by the abstain variant only | `backend/kernel/schemas.py`, `backend/packs/creator/session.py`, `frontend/src/lib/frames.ts`, `frontend/src/components/cards.tsx` | `test_query_abstain_gets_its_own_truth_label`; probe case 12 |

Earlier revisions fixed the fabrications themselves (hardcoded scores, a frame gallery that
compared OCR against itself, truth-label glossary keys that never matched, counters reading
zero from SSE frames). This revision adds the machinery that keeps them fixed: two black-box
probe scripts and the tests above.

## 2. New verification surface

* `python backend/scripts/probe_http.py` — 33 black-box checks against a running server
  (status codes, `Last-Event-ID` resume, quiet-mode clip refusal, frame bytes, `/api/facts`,
  `/api/kernel`, four concurrent sessions). **33/33 pass.**
* `python backend/scripts/probe_dynamic.py` — 18 checks for states no fixture scripts: an
  unrecorded heartbeat, a heartbeat *after* the resume cursor, a vague answer, a late answer, an
  unanswerable creator query, six abandoned sessions followed by a working one. **18/18 pass.**
* `python -m pytest tests -q` — **193 passed, 1 xfailed** (9 new cases in
  `tests/test_audit_fixes.py`).
* `python -u evals/run_evals.py` — 4 runs, 11 cards, 40 model turns, 37 tool calls,
  0 spoilers, 0 unlicensed digits, `PASS - all four hard invariants hold`.

## 3. What the live providers actually did today

* Gemini key valid (60 models listed). `gemini-2.5-flash` is gone (404, "no longer available to
  new users"); `gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite` answer.
* OpenRouter key valid, free tier. `stealth/crouton-alpha` dead (empty completions).
  `nvidia/nemotron-3.5-super-vl:free` and `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
  answer correctly.
* Live pack B (`--llm live --only S2 --budget 8`, empty cassette dir): PASS, 1 card, 8 live
  calls, badge `llm=live`, ~80 s.
* Live pack A (`--asr live --llm live --only u02,u03`): badges `{asr: live, llm: live}`.
  u02 transcribed verbatim; the model then wrote a card containing the token `02`, which the
  commit gate refused (`unlicensed_number`), so **nothing was published**. u03 returned an empty
  transcript and abstained. A live web session (gap S1) asked its boundary question, dispatched
  the investigator, hit its call budget and still committed the deterministic receipt.

## 4. Honest limits, restated

* The fixture match is fictional (`fictional: true` in `/api/facts`); the photographs are real
  cricket photographs, credited in `docs/ATTRIBUTION.md`.
* Scoreboard frames are rendered from the fixture feed, badged `frames: rendered_fixture`.
* The clip is a byte-shaped stand-in, so the byte meter measures policy, not codec cost.
* Inside one episode the supervisor takes one step at a time. The `/agents` page says so and
  never animates fake parallelism; the race view's parallelism is several real sessions.
* The default deployment mode is `RUN_MODE=cached-demo` + `LLM_CASSETTE=replay`: every card still
  commits, from 461 recorded model turns, and no quota is spent when a reviewer opens the link.
  `RUN_MODE=live-demo` switches to the real providers.
* No git remote was configured in the build sandbox, so this archive — not a GitHub push — is the
  update. Keys are blanked in it.

---

# Revision 3 — model-driven, not fixture-shaped

What changed because "the backend is full of deterministic — if a new case comes, will it handle?"

## 1. A real model pool, not one hard-coded model

`backend/kernel/router.py` (`MultiLLM`) puts every configured provider behind one interface: one
member per model string, round-robin per call, a per-member cooldown after a timeout/429/empty
completion, and a report of the model that actually served each turn. `backend/kernel/factory.py`
builds the pool from the keys that exist, so a missing key removes a member instead of breaking
the page. Layers are explicit and per-session: `replay` (recordings only), `live` (the pool),
`off` (no transport). `GET /api/models` prints the pool without spending a request.

Measured today, with both keys live: `served_models = [gemini-3-flash-preview,
gemini-3.1-flash-lite]`, layer badge `live`. OpenRouter's free routes answered earlier and then hit
their 50-requests/day ceiling (`429 free-models-per-day`) and Gemini's free tier hit
`429 RESOURCE_EXHAUSTED`; both are handled as cooldowns, not as crashes.

## 2. Cases nobody scripted

`backend/app/custom.py` plus `scenario: "custom"` on `POST /api/session`:

* **Gap** — pick any ball the feed holds and what happened (ad, stall, app switch, late join). The
  boundary is re-estimated from synthesised heartbeats by the same rules, so the agent reasons over
  a window it has never seen. An unknown ball is refused by name with `known_balls`.
* **Creator** — type any line and it is checked like a live one. Typed words are badged
  `asr: typed`; they are never reported as a transcription.
* Both are reachable from the UI ("Run my own situation", "Check this line").

## 3. The prediction path, repaired

A live run said *"Aarav Sen agle over mein ek six maar dega"*; the model classified it as a
prediction but left `threshold` empty, the ledger correctly refused to store a target-less
prediction, and the fan saw nothing. `complete_from_text` now reads a count the sentence already
stated ("ek" → 1, "do" → 2) and reports which fields it filled, so a completion is never mistaken
for model output. Verified end to end: a prediction card, then a settlement card
("MISSED: 0 boundaries in over 17").

## 4. The interface, rewritten for a layman

* The panel that used to print the wire format and a raw JSON blob per row now prints one plain
  sentence per step (`src/lib/human.ts`), with the technical detail behind a disclosure.
  "supervisor_error" reads as "could not reach a model for this step — the kernel carried on
  without one".
* Scenario expectations are words, not `{"card_kind": "receipt"}`; the mode toggle explains itself;
  the SSE badge says "updating live" instead of `sse open`; the heartbeat buttons say "An ad
  starts" instead of `+ad_start`.
* Copy was cut across the home page; the hero photograph drifts slowly and stops entirely under
  `prefers-reduced-motion`.
* Two mislabelled statistics on the creator page ("Greetings dropped with no model call" counting
  something else) were corrected.

## 5. Verification, re-run after the changes

`pytest tests -q` → **227 passed, 1 xfailed** (34 new cases: every ball × every interruption, the
late-join window, typed-ASR labelling, prediction completion, pool rotation, cooldown, served-model
reporting, all-down raising). `probe_http.py` **33/33**; `probe_dynamic.py` **18/18**;
`evals/run_evals.py` **PASS** (4 runs, 11 cards, 36 model turns, 0 spoilers, 0 unlicensed digits).

## 6. What is still not real, and says so

* The match, the phone telemetry, the creator script and the audio are fixtures. They are
  fabricated on purpose and labelled on screen.
* A replayed model turn only exists for a state that was recorded. When a run reaches a state the
  store does not hold, the trace says so in one line and the kernel finishes the card in code —
  never by inventing words. The honest "what is real here" table on the home page lists this.
* No deploy URL and no git remote exist in this sandbox; the archive is the update.

---

# Revision 4 — tested against the models that actually answer

Everything below was found by running the product against the live providers, not by reading the
code. Each item is a bug that a reviewer would have hit.

## 1. The pool declared itself exhausted while a model was answering in 0.4 s

`pool exhausted for tag=supervisor` appeared mid-episode even though `gemini-3.1-flash-lite`
answered every single-shot probe. Two causes, both fixed in `backend/kernel/router.py`:

* A single failure parked a member for the whole cooldown (240 s), so one bad turn took the only
  healthy model out of the run. Cooldown is now graded (15 s, 30 s, 60 s ... up to the cap) and
  resets on success.
* There was no recovery path. When every member looks unavailable the router now makes exactly one
  *last chance* attempt on the member that has been cooling longest — never retrying a member
  twice inside one call, so a dead provider still costs one attempt per turn, not two.

## 2. A spent daily quota looked like a crash

Google's free tier is ~20 requests per model per day and OpenRouter's is 50 per day; both run out
during a demo. The 429 body says exactly when it recovers ("Please retry in 17h2m25s"), so the
router now parses it: that member is parked for the stated duration, `GET /api/models` reports
`capacity: quota_exhausted` with a `quota_until` timestamp, and the session's `llm_note` says in
words that the pool had no capacity for that step. The page shows it as one line
("answered by live models" / "answered from recorded model turns" / "answered with no model at
all — code only"), so a fan never has to guess what they are looking at.

## 3. "142 for 2" was invisible to the score verifier

The verifier only recognised `142/2`. A live run of "142 for 2, yeh match abhi bhi khula hai" was
classified as opinion and skipped — the exact failure the pack exists to prevent. `SCORE_RES` now
covers `142/2`, `142-2`, `142: 2`, `142 for 2`, `142 pe 2` and `142 runs for 2 wickets`, with a
guard (runs ≥ 10, wickets ≤ 10) so "3 for 2 in the third over" and "1420 for 2" are not read as
scores. Re-run: **conflict card, "Creator said 142/2 — feed says 141/3"**.

## 4. A custom gap case could only ever say "you missed nothing"

Two bugs stacked: the synthesised heartbeats painted right up to the ball before the resume point
(leaving a one-ball window that only contains the resume ball, which the clamp may never show),
and `_neighbours` allowed the resume ball itself to be a boundary candidate. Both fixed; a custom
case now produces a real receipt — "You missed 16.5-16.6: 146/4 -> 147/5" — and a test asserts no
candidate is ever at or after the resume cursor.

## 5. The deterministic receipt was refused for citing the boundary ball

When the model's justification named the ball the fan last saw (`[evt_068]`), the receipt was
rejected with `cites_uncited_event` and the fan got no receipt at all. A justification that names a
real, in-window ball is a *citation*: it is now added to the card's citations (and shown), while
anything at or after the resume cursor still fails.

## 6. A live supervisor could burn the whole budget repeating itself

Observed live: three `commit` decisions with no card attached, then three critic rejections of the
same corrected card, then pool exhaustion. The kernel now bounds both: a second empty commit and a
second critic refusal of the same card end the episode and let the pack's deterministic card
commit, with a `guardrail` frame naming the rule.

## 7. What was verified live today, and what was not

| case | result |
|---|---|
| gap S1, live | receipt committed, 2 model turns, 0 refusals |
| gap custom (16.3, ad), live | receipt "You missed 16.5-16.6: 146/4 -> 147/5" |
| creator, all 12 segments, live | 2 cards (confirmed claim + conflict), 3 verifications, live ASR |
| creator, prediction, live | prediction card + settlement "MISSED: 0 boundaries in over 17" |
| creator, wrong score, live | conflict card "Creator said 142/2 — feed says 141/3" |
| creator, opinion, live | insight card, labelled opinion |
| creator, off-topic, live | 0 cards in 1.4 s — triage dropped it with no model call |

The free tiers were then exhausted (Gemini 20/day per model, OpenRouter 50/day), so the replay
layer is what the offline suite exercises: `pytest` **243 passed, 1 xfailed** (46 new cases),
`probe_http.py` **33/33**, `probe_dynamic.py` **18/18**, `evals/run_evals.py` **PASS**.

Two honest notes about this revision's artifacts:
* Nine recorded turns were re-recorded (470 cassettes total) because the boundary fix changed the
  S2/S3 prompts; the recordings are real model output, made with the same live key.
* The screenshots in `docs/screenshots/` were captured before this revision and could not be
  recaptured here: the sandbox reset removed the browser system libraries and there is no root to
  reinstall them. The bundle itself was rebuilt and verified by string-and-serving checks, not by
  a fresh screenshot.

---

# Revision 5 — found by looking at the product, not at the tests

Every item below was caught by driving a real browser against the running app. Two of them were
spoiler bugs, which is the one class of bug this product cannot ship with.

## 1. The receipt listed the ball the fan was watching, as a ball they missed

`screenshot gap-custom.png` showed a card headed **"You missed 16.1-16.3"** whose only bullet was
`16.3 WICKET`, while the shield beside it said *"nothing at or past 16.3 has been released"*. The
fan resumed **at** 16.3, so that is the ball they are watching, not a ball they missed.

Cause: `missed_window()` ended at `resume_cursor + 1`, and `voi.receipts_for()` did the same, so the
resume ball was inside the window by construction. Both now end one ball short of the cursor. The
headline span also used to claim `first..resume` whatever the bullets said; it now ends at the last
ball actually missed. The end-of-window score is still read *from* the resume event, because that
is what the scoreboard shows at the fan's position.

Three new tests lock the invariant: no card of any kind may cite the resume ball as missed, the
window never crosses the cursor, and the tally can never say "4 balls" above a card with 3 bullets.

## 2. A deterministic fallback that was promised and never delivered

A real live S1 run ended `stopped_noop` with **zero cards**. The trace said *"the critic refused the
same card twice; the pack's deterministic card will be used"* and then nothing was committed: the
fallback is only owed to the fan when the guardrail sets `kernel_must_commit`, and neither loop
guardrail set it. Both do now, and a test drives a stub critic that refuses forever to prove the fan
still gets a receipt.

## 3. Two different failures, one wrong sentence

The page said *"the model pool had no capacity for this step"* in three unrelated situations, and
once printed the contradiction **"no capacity (available)"**: a 429 on one member while another was
healthy. `llm_note` now distinguishes a replay with no recorded turn from a spent quota from a
plain rate limit, and "no capacity" is only printed when the pool itself reports `quota_exhausted`
or `cooling_down`.

## 4. A poisoned recording deleted the only copy

`OpenRouterLLM` deleted a cassette entry that failed to parse and then re-recorded it. If the
re-record failed - a spent daily quota, no network - the recording was simply gone, and the path it
served stayed broken. It is now flagged for replacement and only removed by a successful recording
of the same key.

## 5. The shield said "at or past" while checking only "after"

`spoilerState()` compared event ids with `>`, then claimed nothing "at or past" the cursor had been
released. Both the wording and the badge legend now match what is actually enforced.

## 6. Coverage is measured, not asserted

`backend/scripts/cassette_coverage.py` drives every path over HTTP against the running server and
prints which model turns had to fall back. `docs/CASSETTES.md` records the last pass and explains
each provenance line the UI can show. Current store: **580 recordings**.

```
ok  gap/S1 ans=0/1, S2 ans=0/1, S3 ans=0/1, creator/ALL      (every browser path, 0 errors)
FALLBACK 4 of 10                        the no-answer and non-browser API paths
```

The four `FALLBACK` rows are paths where the fan never answers the boundary question. They still
produce a receipt (the boundary then comes from telemetry), but a model turn in each was never
recorded because all five providers hit their daily free-tier cap while this pass was running.
They are listed as missing rather than hidden, and the tool that proves it ships with the repo.

## 7. Screenshots that exist

The nine files in `docs/screenshots/` were recaptured against this build with a real browser, 0
console errors, and `docs/capture_screenshots.py` is the script that did it, so they can be
regenerated offline at any time. The older `.jpg` set, which showed the pre-rewrite copy, is gone;
nothing in the repo links to it any more.

Verification after all of the above: `pytest` **250 passed, 1 xfailed**, `probe_http` **33/33**,
`probe_dynamic` **18/18**, `evals/run_evals.py` **PASS**, `tsc --noEmit` clean, `npm run build`
clean.

---

# Revision 6 - 2026-11-05 (evening)

## 1. Five model turns no provider could record are now recorded

The free-tier pool was exhausted for the whole day (OpenRouter `429 free-models-per-day`,
reset header `1793923200000` = 2026-11-06T00:00:00Z; Gemini likewise), so the two supervisor
turns and three critic turns that a `cassette.drop()` had erased were written by the reviewing
AI agent instead of being left as fallbacks. They are marked, not disguised: each file says
`"model": "arena-agent (hand-authored)"` with zero provider usage, and
`docs/handauthored/<key>.prompt.json` stores the verbatim prompt, schema and temperature it
answers, so the key can be recomputed and the pairing checked. `docs/CASSETTES.md` lists all
five and what each decides. This is the only entry in the 585-turn store a provider did not
write; when the quota resets, the re-recording recipe replaces them with real completions.

## 2. A jury-pane heartbeat followed the fan out of the room

`POST /api/session/{sid}/interrupt` appended heartbeats to `web.session.scenario` - which for
every standard scenario was the **shared** fixture object. One probe's injected stall
therefore changed the telemetry of every later session: their supervisor prompts drifted off
the recorded cassette keys and the demo silently fell back. That is also why revision 5's
coverage pass reported keys as "missing" that had never been in the store. Each session now
binds its own copy of the scenario (`GapSession.run_scenario`),
`test_a_jury_pane_heartbeat_never_reaches_a_later_session` locks it, and the coverage table
in `docs/CASSETTES.md` is measured *after* `probe_dynamic.py` has exercised the interrupt
endpoint on the same server: still **10/10**.

## 3. The S1 golden still demanded the ball the fan was watching

Revision 5's window fix stopped the receipt at the resume ball (no card may cite it), but the
fixture's `expected` block still required `evt_071` - the resume ball itself - in S1's
citations, so `run_pack_b.py --llm replay` failed S1 with "missing material event". No gate
ran that script, so it went unnoticed. The golden now requires the two material balls the fan
actually missed (wicket 16.3, four 16.4) and *excludes* the resume ball, the builder that
produces the fixture says the same, and the failure line reads "at or past the fan's
position" like the rule it enforces. All three golden scenarios pass again.

## 4. Coverage: 10/10, correcting revision 5's 4/10

Revision 5's table quoted a pass whose server had already been polluted by the interrupt bug
above; of its five "missing" keys, three were artifacts of that pollution. With isolation in
place and the five real gaps filled, every path - all six gap answer variants, the
twelve-segment creator stream, and the three never-answered API paths - serves every turn
from disk with zero model errors. `docs/CASSETTES.md` was rewritten: its old table was stale,
and its claim that "nothing in the store was written by hand" is no longer true, so it now
says exactly which five entries were and why.

## 5. Every quoted number re-measured

`pytest` counts, evals turn counts, both pack drivers, the cassette total, the line counts
and the deploy-bundle measurement in `README.md`, `docs/SUBMISSION_NOTE.md`, `docs/SPEC.md`
and `docs/TECHNICAL_DOCUMENT.{md,html,pdf}` were re-run on this checkout and corrected
(251 passed / 585 cassettes / 36 model turns / 24 tool calls / pack B 22 llm calls /
pack A 58 llm calls / bundle 763 files).

Verification after all of the above: `pytest` **251 passed, 1 xfailed**; coverage **10/10**
(also after `probe_dynamic.py`); `probe_http` **33/33**; `probe_dynamic` **18/18**;
`evals/run_evals.py` **PASS** and `--llm off` **PASS**; `run_pack_b.py --llm replay` all three
golden scenarios **PASS** (22 llm calls) and `--llm off` **PASS** (0 calls);
`run_pack_a.py --asr cached --llm replay` **8 cards, 58 llm calls**; `tsc --noEmit` clean;
`npm run build` clean (`index-CtE3RJOB.js` 239.54 kB, `index-C1SNa2Cc.css` 41.55 kB).

---

# Revision 7 - 2026-11-06

## 1. The provenance line claimed a live model answered when none did

A custom case starts on `layer=live` with the note "the live model pool was used". When the
pool then died on a 429, `on_trace` refused to overwrite that note (`and not self.llm_note`),
so the page read **"answered by live models | ... the live model pool was used"** above a badge
that said `unavailable` and a card that was the deterministic fallback. Three fixes, each with
a test:

* the truthful failure note now **replaces** the optimistic layer note, still said once
  (`test_the_failure_note_replaces_the_optimistic_layer_note`);
* `AnswerSource` derives its sentence from `badges.llm` - what actually served - instead of
  the layer that was merely requested, and shows "the model did not answer this step" when the
  transport failed;
* the wording only claims "no capacity" when the pool's own status says so (unchanged from
  revision 5).

The failing scenario was reproduced over HTTP before the fix and after it: same dead pool,
same fallback card, note now `a model call for this step was rate-limited; the kernel answered
from the feed instead of inventing a line`.

## 2. Live re-record attempted, honestly failed, store unharmed

The free-tier quota reset at 2026-11-06T00:00:00Z, so the five hand-authored turns were moved
to a backup and all ten paths were driven through a `live-demo` + `record` server. The fresh
50-request daily allowance was spent in minutes with **zero** parseable completions (prose
replies and 429s; unparseable replies are never written), `cassette_coverage` fell back on the
unrecorded paths exactly as designed, and the store was verified **byte-identical** to the
shipped revision before the five entries were restored. Coverage back to 10/10, provenance
pairings re-verified 5/5. Next window: `1794009600000` = 2026-11-07T00:00:00Z. The attempt and
its outcome are recorded in `docs/CASSETTES.md`.

## 3. The sandbox reset, and everything reinstalled from the repo

The overnight environment reset removed the pip packages, `frontend/node_modules` and
`frontend/dist` (none of which live in the workspace snapshot). `pip install -r
requirements.txt`, `npm install && npm run build` restored both - the build reproduced the same
content hashes, which is itself a check that the frontend sources did not drift.

Verification after all of the above: `pytest` **252 passed, 1 xfailed**; coverage **10/10**;
`probe_http` **33/33**; `probe_dynamic` **18/18**; `evals` **PASS**; `run_pack_b --llm replay`
all three **PASS**; browser capture of all nine screenshots **0 console errors**, with the
custom-case provenance line now reading "the model did not answer this step | a model call for
this step was rate-limited; the kernel answered from the feed instead of inventing a line";
`tsc --noEmit` clean; `npm run build` clean (`index-BL-6qS5E.js` 239.86 kB, `index-C1SNa2Cc.css`
41.55 kB).
