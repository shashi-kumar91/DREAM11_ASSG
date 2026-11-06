# Screenshots

Every file here was captured with a real browser (Chromium via Playwright) against
`uvicorn backend.app.server:app` on this checkout, in the default `RUN_MODE=cached-demo` +
`LLM_CASSETTE=replay` mode. **0 console errors** during the capture pass. Nothing is retouched,
nothing is staged: each page shows the frames its own run produced.

The page's own provenance line is visible where it matters ("answered from recorded model turns"
vs "answered by live models"), so a reader can tell which run made the picture.

| file | what it shows |
|---|---|
| `home.png` | landing page: the fan problem in one line, the two artifacts, three steps |
| `gap-question.png` | the one question Gap Receipt asks, with its two answers |
| `gap-run.png` | the catch-up receipt after an answered run - headline, the balls that were missed, required rate before/after, the "nothing after 16.6 is included" clamp, and the spoiler shield agreeing with it |
| `gap-behind.png` | "Show the working" open: what happened, step by step, and what it cost |
| `gap-custom.png` | "Run my own situation" - a ball and an interruption the fixtures do not contain, run on the live model pool. This one honestly reports "nothing key missed" |
| `creator-run.png` | Watch Along after all 12 segments: opinion, verified claim and conflict cards, each labelled |
| `creator-custom.png` | "Check this line": words typed by the fan, checked against the feed - a wrong score becomes a conflict card |
| `agents.png` | `/agents` - three real sessions side by side, with the permission wall per specialist |
| `mobile-gap.png` | the gap page at 414 px |

Regenerate them with the browser + server running:

```
RUN_MODE=cached-demo LLM_CASSETTE=replay python -m uvicorn backend.app.server:app --port 8931
python docs/capture_screenshots.py          # writes into this folder
```
