"""Probe candidate models for text, audio-in, and structured-output support."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
from google import genai  # noqa: E402

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

TEXT = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview",
        "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]

for m in TEXT:
    try:
        r = client.models.generate_content(
            model=m, contents="Reply with exactly: CONNECT_OK")
        print(f"TEXT {m}: {r.text.strip()[:40]}")
    except Exception as e:
        print(f"TEXT {m}: FAIL {type(e).__name__} {str(e)[:140]}")

print("\n--- audio transcription probe ---")
for m in ["gemini-3.5-transcribe", "gemini-2.5-flash-lite"]:
    try:
        r = client.models.generate_content(
            model=m,
            contents=["Translate/transcribe this Hindi audio.",
                      {"mime_type": "audio/wav", "data": b"RIFF\x00\x00\x00\x00WAVEfmt "}])
        print(f"AUDIO {m}: ok -> {str(r.text)[:80]}")
    except Exception as e:
        print(f"AUDIO {m}: {type(e).__name__} {str(e)[:180]}")
