# Replay answers

Recorded API responses for the public demo's example questions, one file per (user, question, contract), plus
`index.json` listing what each demo user may ask. In replay mode (`CC_MODE=replay`) the API serves these instead of
calling the model, so the demo costs nothing per visitor; permissions and masking are already applied because they
were recorded through the API as each user. Re-record with `uv run python scripts/record_replay.py` (DECISIONS D16).
