"""Reality check: does real Gemini ASR handle the synthetic Hinglish creator audio?

Run: python backend/scripts/probe_asr.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from backend.adapters.asr import GeminiASR  # noqa: E402
from backend.kernel.llm import GeminiLLM  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures"
rows = json.loads((FIX / "transcript_expected.json").read_text(encoding="utf-8"))
meta = json.loads((FIX / "match_meta.json").read_text(encoding="utf-8"))
llm = GeminiLLM(cassette_mode="record")
asr = GeminiASR(llm)

def norm(s: str) -> str:
    return " ".join(s.lower().replace(",", " ").replace(".", " ").split())

scores = []
for r in rows:
    audio = (FIX / r["wav"]).read_bytes()
    res = asr.transcribe(audio, meta["keyterms"], hint_id=r["id"])
    g, h = norm(r["text"]), norm(res.text)
    gt, ht = set(g.split()), set(h.split())
    overlap = len(gt & ht) / max(1, len(gt))
    scores.append(overlap)
    flag = "NOISY" if r.get("noisy") else "     "
    print(f"\n{r['id']} [{flag}] conf={res.confidence:.2f} word-overlap={overlap:.2f} ({res.model})")
    print(f"  GOLD : {g}")
    print(f"  ASR  : {h}")
    if res.uncertain_words:
        print(f"  UNCERT: {res.uncertain_words}")

print(f"\nmean word overlap {sum(scores)/len(scores):.2f} over {len(scores)} segments")
print(f"live LLM calls: {llm.calls}")
