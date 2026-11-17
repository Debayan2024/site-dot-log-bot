# Eval results

Hand-labelled set: `eval/samples.jsonl` (33 invented messages, expected outputs written before any model run).
Run: `python eval/run_eval.py --write --note "what you changed"`

For each run record: date, model, prompt version, field accuracy, message accuracy, each failure and its cause,
what you changed in the prompt, and the before/after numbers. Keep the honest failure list.

**No runs recorded yet.** The first run needs `GEMINI_API_KEY`; numbers below are appended by `run_eval.py --write`.
