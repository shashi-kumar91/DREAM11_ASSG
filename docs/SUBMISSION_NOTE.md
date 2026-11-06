# Submission note

The note below is the text to paste into the submission form. **199 words**, counted as
whitespace-separated tokens of the three note paragraphs (every standalone hyphen is
charged as a word, so a normal counter reads lower). The heading and the Links section
below it are not part of the note.

---

A fan on a phone loses the stream to an ad, a stall or a late start, and comes back mid-chase asking: what did I miss? Chatbots guess. A highlight reel spoils the ball they just missed. Second problem, attention: Dream11 wants creator-led engagement, but forcing a fan to load video for one score line costs data they never agreed to spend.

We shipped one agent kernel with two artifacts. Gap Receipt (FanCode-shaped) estimates the watched/missed boundary from the player's own heartbeat log, asks the fan one specific question only when the candidate boundaries would change the answer, then commits a receipt - score, wickets, required-rate shift, frames read out of the pixels - or an explicit no-op when nothing key happened. Nothing past the fan's playback cursor can be released. Watch Along (Dream11) transcribes the creator's Hinglish audio, cross-checks each claim against the feed and publishes verified or conflict cards, plus predictions it settles on later balls.

Tools: Python/FastAPI and Server-Sent Events behind a React/Vite/TypeScript player; an OpenRouter-hosted model runs a supervisor plus capability-scoped specialists; Gemini handles audio transcription; numpy/Pillow read the scoreboard. The model proposes; code verifies and commits, and never authors a number or a badge.

---

## Links

- Deployed artifact (no login, opens straight into the player): `https://REPLACE-ME`
- Repository: `https://REPLACE-ME`
- Agent room (inside the same artifact): `https://REPLACE-ME/agents`

Both links are placeholders until the deploy is published; nothing here claims a live URL.
The repo runs without a key: `python -m pytest tests -q` (252 passed, 1 xfailed), then
`python backend/scripts/run_pack_b.py --llm off` (3 cards, `llm calls: 0`, badge
`llm: unavailable`), then `python -u evals/run_evals.py` (11 cards, 36 model turns, 24 tool
calls, 0 spoilers, 0 unlicensed, `PASS`), then two black-box probes against a live server
(`probe_http.py` 33/33, `probe_dynamic.py` 18/18). Measured on this checkout, not quoted from a
doc.

## What the reviewer should open

1. `/` — the fan problem in one screen, with the real scorecard and the ball strip.
2. `/gap` → pick a situation → run: watch the boundary question, the receipt, and the spoiler
   shield stay closed past the resume cursor.
3. `/creator` → "Check all 12 segments": claims verified against the feed, one conflict shown as
   a conflict, predictions settled on later balls.
4. `/agents` → "Race all three situations": three real sessions side by side, each drawing its
   own supervisor and five specialists from the frames it publishes. "Read at human pace" is
   labelled pacing; the frames are the server's.
