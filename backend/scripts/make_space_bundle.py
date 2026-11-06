"""Assemble the folder that becomes a public, no-login Space (Hugging Face Docker SDK).

The repository is a monorepo with tests, traces and scripts that a hosted demo does not need, and a
Hugging Face Space reads its configuration from the YAML header of the README at the root of the
repo it builds. This writes a clean deploy directory: the same `Dockerfile`, only the code the
server imports, the cassette store the replay modes read, the frontend sources the image builds,
and a README that carries the Space header.

    python backend/scripts/make_space_bundle.py --dest space
    hf repo create <user>/<space> --space --sdk docker   # once
    hf upload <user>/<space> . . --repo-type space       # from inside the bundle, or git push

Nothing here contacts a host and no secret is copied: `.env` is refused by name and by suffix, and
the key arrives later as a Space secret (see DEPLOY.md).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# What the running server imports. `tests/cassettes` is the recorded model turns; the rest of
# `tests/` is source-level test code that has no business in a hosted demo.
CODE_DIRS = ["backend/kernel", "backend/packs", "backend/adapters", "backend/metering",
             "backend/app", "backend/fixtures", "backend/scripts", "tests/cassettes"]
CODE_FILES = ["Dockerfile", "requirements.txt", ".dockerignore"]
# Vite is configured at the repository root, not inside `frontend/`, so the image's UI stage needs
# these next to the sources. `frontend/dist` is optional: the Dockerfile rebuilds it, and shipping
# a prebuilt copy only lets the bundle run without a node toolchain.
UI_DIRS = ["frontend/src", "frontend/public", "frontend/dist"]
UI_FILES = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
            "frontend/index.html"]
REQUIRED = CODE_DIRS + CODE_FILES + ["frontend/src", "frontend/index.html", "package.json",
                                     "vite.config.ts"]

# A Space header. app_port must match the EXPOSE in the Dockerfile, and sdk: docker is what makes
# the host build the image instead of looking for a Gradio app.
SPACE_README = """---
title: Dream11 Watch Along & FanCode Gap Receipt
emoji: 🏏
colorFrom: indigo
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# One kernel, two live-match fan artifacts

Pick a route at the top of the page:

- **Gap Receipt** - you came back mid-chase after an ad, a stall or a late open. The agent
  estimates which ball you last actually saw, asks one question only when the answer would change
  the receipt, and returns what you missed with the score and required-rate shift, or an honest
  "nothing key missed".
- **Watch Along** - a live Hinglish creator stream turned into labelled cards: verified against
  the feed, in conflict with it, opinion, prediction (settled later against real balls).

The rule the whole build follows: **the model proposes; deterministic code verifies, labels and
commits.** No model authors a number, a truth badge, or an event the fan has not reached, and every
card carries a provenance badge (`live` / `cassette`) so you can see which path produced it.

Match feed, telemetry and scoreboard frames are replay fixtures; the pixel OCR, agent loop,
guardrails, commit gate and byte metering are real code, running on this container.
"""

IGNORE = shutil.ignore_patterns("__pycache__", "*.py[cod]", ".pytest_cache", "node_modules")


def refuse_secrets(dest: Path) -> None:
    for p in dest.rglob("*"):
        if p.name == ".env" or p.name.startswith(".env.") or p.name.endswith((".key", ".pem")):
            sys.exit(f"refusing to ship a secret file: {p.relative_to(dest.parent)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="space", help="output directory (rebuilt from scratch)")
    a = ap.parse_args()
    final = (ROOT / a.dest).resolve()
    if final == ROOT:
        sys.exit("refusing to bundle into the repository root")
    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            sys.exit(f"missing {rel} - the bundle would ship a broken server")

    # Built in a sibling directory and swapped into place, so a failed run never leaves a
    # half-copied bundle that a reviewer then deploys. The suffix is per process because a previous
    # run's staging directory can still be held open by a server that was started from it.
    staging = final.with_name(f"{final.name}.{os.getpid()}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for rel in CODE_DIRS + UI_DIRS:
        if (ROOT / rel).is_dir():
            shutil.copytree(ROOT / rel, staging / rel, ignore=IGNORE)
    for rel in CODE_FILES + UI_FILES:
        if not (ROOT / rel).exists():
            continue
        (staging / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, staging / rel)
    for pkg in ("backend", "tests"):
        f = staging / pkg / "__init__.py"
        if not f.exists():
            src = ROOT / pkg / "__init__.py"
            f.write_text(src.read_text(encoding="utf-8") if src.exists() else "",
                         encoding="utf-8")

    (staging / "README.md").write_text(SPACE_README, encoding="utf-8")
    refuse_secrets(staging)

    if final.exists():
        try:
            shutil.rmtree(final)
        except OSError as exc:
            sys.exit(f"{final} is held open (a server is running from it): {exc}\n"
                     f"The verified bundle is at {staging}; deploy that, or stop the server.")
    os.replace(staging, final)

    files = [f for f in final.rglob("*") if f.is_file()]
    print(f"bundle: {final}")
    print(f"files : {len(files)} | {sum(f.stat().st_size for f in files) / 1024:,.0f} kB")
    print(f"turns : {len(list((final / 'tests/cassettes').glob('*.json')))} cassettes")
    print("ui    : " + ("prebuilt dist included" if (final / "frontend/dist/index.html").exists()
                        else "image builds the UI from source"))
    print("next  : hf repo create <user>/<space> --space --sdk docker, then"
          " hf upload <user>/<space> . . --repo-type space  (see DEPLOY.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
