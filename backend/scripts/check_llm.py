"""Verify LLM connectivity for the agent runtime. Lists models and runs one short prompt."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("GEMINI_API_KEY missing from .env")
        return 1
    print(f"key present, length={len(api_key)}, prefix={api_key[:3]}...")

    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        models = list(client.models.list())
        names = [m.name.split("/")[-1] for m in models]
        print(f"OK - {len(names)} models visible")
        interesting = [n for n in names if "2.5" in n or "3" in n]
        for n in interesting[:40]:
            print("  ", n)
    except Exception as exc:
        print(f"models.list failed: {type(exc).__name__}: {str(exc)[:500]}")
        return 2

    for candidate in ["gemini-2.5-flash", "gemini-flash-latest"]:
        try:
            r = client.models.generate_content(
                model=candidate, contents="Reply with exactly: CONNECT_OK"
            )
            print(f"{candidate}: {r.text.strip()[:80]}")
            return 0
        except Exception as exc:
            print(f"{candidate} generate failed: {type(exc).__name__}: {str(exc)[:300]}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
