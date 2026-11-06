# Deploying the artifact to a public link (no login)

One image, one port, all configuration from environment variables. `frontend/dist` is built in
stage 1 of the `Dockerfile` and FastAPI serves it at `/` alongside `/api`, so the container is a
single origin — there is no second host to point the browser at and nothing to log into.

## What the container needs

| variable | why | required? |
|---|---|---|
| `RUN_MODE` | `cached-demo` replays every recorded model turn; `live-demo` calls the provider | no — defaults to `cached-demo` |
| `LLM_BACKEND` | `openrouter` or `gemini` | no — defaults to `openrouter` |
| `OPENROUTER_API_KEY` | only for `live-demo` | no — without it the run degrades to cassettes and the card badges say `llm: cassette` |
| `OPENROUTER_MODEL`, `OPENROUTER_MODEL_CHAIN` | which model serves each turn and the failover order | no — sensible defaults in `backend/kernel/openrouter.py` |
| `GEMINI_API_KEY` | only for live audio transcription (Pack A) | no — falls back to the badged cached transcript |
| `ASK_TIMEOUT_S` | how long the fan gets to answer the boundary question | no — 90 in the image |
| `MAX_SESSIONS` | concurrent demo sessions before new ones are refused | no — 12 |

`cached-demo` is the deploy default on purpose: a reviewer's link must not die because a free-tier
daily quota ran out on the server, and the cassette path is a replay of real model turns (proven by
`python backend/scripts/compare_runs.py record replay`), not a scripted answer.

## Hugging Face Spaces (free, Docker SDK, no account setup beyond a token)

The `Dockerfile` already reads `$PORT` and exposes 7860, which is what a Space expects. A Space also
requires the `sdk: docker` header in the README **at the root of what it builds**, and this repo's
README is reviewer documentation rather than Space configuration. So do not push the monorepo
directly: build the deploy directory, which carries the header, the code the server imports, the
cassette store and the UI.

```
python backend/scripts/make_space_bundle.py --dest space     # ~20 MB, no .env, refuses to copy one
```

It prints the cassette count and whether a prebuilt `frontend/dist` went along; run the bundle
locally before uploading, because that is the real test of self-containment (it proves nothing the
image would have to compile is missing):

```
cd space && RUN_MODE=cached-demo python -m uvicorn backend.app.server:app --port 8152 &
curl -s localhost:8152/api/health          # cassettes>0, frontend_built:true
python -u backend/scripts/drive_http.py --base http://127.0.0.1:8152 --pack gap --scenario S1 \
    --answer "No, I left at 16.2, recap everything"      # must print PASS
```

Then publish from `space/`:

```
hf auth login                              # or: hf auth login --token <read/write token>
hf repo create <user>/dream11-gap-receipt --repo-type space --space-sdk docker
hf upload <user>/dream11-gap-receipt . . --repo-type space
```

The build takes a few minutes (stage 1 runs `npm ci && npm run build`, stage 2 installs the
requirements). Add `OPENROUTER_API_KEY` as a Space secret only if you want the badge to read
`live`; without it the demo still runs and the badge honestly says `cassette`. The public URL is
`https://<user>-dream11-gap-receipt.hf.space` — no login, opens straight into the player. Re-upload
to rebuild; the Space build log is the first place to look if the page is blank.

After it is up, verify the *public* link the same way, and prove the hosted artifact is the same
run as the local one rather than a lucky replay:

```
python -u backend/scripts/drive_http.py --base https://<user>-dream11-gap-receipt.hf.space \
    --pack gap --scenario S1 --answer "No, I left at 16.2, recap everything" \
    --log tmp/hosted.jsonl
python backend/scripts/diff_frames.py backend/out/http_S1_live2.jsonl tmp/hosted.jsonl
```

## Render (free web service)

New → Web Service → connect the repo → Runtime **Docker**, and set the env vars above in the
dashboard. Render injects `PORT`, which the `CMD` already uses. Free instances sleep after idle,
so the first hit after a quiet period costs ~50 s of cold start — send it a `/api/health` ping
from the submission notes so the reviewer never meets it cold.

## Fly.io

```
fly launch --image local/dre-agent --no-deploy
fly secrets set OPENROUTER_API_KEY=...    # never in fly.toml
fly deploy
```

`flyctl` picks up `EXPOSE 7860`; the app answers on `$PORT` so no config edits are needed.

## Before you send the link

```
python -m pytest tests -q                                    # must be green
python -u backend/scripts/run_pack_b.py --llm off            # 0 model calls, still 3 cards
python -u evals/run_evals.py                                 # the four hard invariants, PASS
python -u backend/scripts/compare_runs.py openrouter record replay   # model turns, not fallback
python backend/scripts/make_space_bundle.py                  # and it must serve on its own port
```

and then against the deployed host:

```
python -u backend/scripts/drive_http.py --base https://<the-link> --pack gap --scenario S1 \
    --answer "No, I left at 16.2, recap everything"
```

That last command is the acceptance test for a public link: it opens a session over real HTTP,
answers the fan's question, and exits non-zero if the fan would have been left with silence.
