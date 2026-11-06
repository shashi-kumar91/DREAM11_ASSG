# Hand-authored model turns

The five files here are the verbatim prompts for the five cassette entries no provider could
record: on 2026-11-05 the whole free-tier pool was at its daily cap (OpenRouter
`429 free-models-per-day`, reset `1793923200000` = 2026-11-06T00:00:00Z), and those turns were
written by the reviewing AI agent instead of being left as deterministic fallbacks.

Each `<key>.prompt.json` contains:

* `system`, `prompt`, `schema_json`, `temperature` — the exact inputs the cassette key is
  computed from;
* `key` — the resulting key, which is also the filename in `tests/cassettes/`;
* `authored_reply` — what was written into the cassette file's `"text"`;
* `authored_by` — `arena-agent (hand-authored)`, matching the cassette's `"model"` field;
* `reason` — why a hand-authored entry exists at all.

Verify the pairing in one line:

```python
import hashlib, json
d = json.load(open("docs/handauthored/<key>.prompt.json"))
k = hashlib.sha256("\x00".join([d["system"], d["prompt"], d["schema_json"],
                                f'{d["temperature"]:.2f}']).encode()).hexdigest()[:32]
assert k == d["key"]
assert json.load(open(f"tests/cassettes/{k}.json"))["text"] == json.dumps(d["authored_reply"])
```

They are the only entries in the 585-turn store that a provider did not write, and
`docs/CASSETTES.md` says which coverage rows depend on them. Replacing them with real
recordings after a quota reset needs nothing but the re-recording recipe in that file — the
prompts above are byte-exact what a live pass has to hit.
