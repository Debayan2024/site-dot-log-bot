"""Run the extractor over eval/samples.jsonl and report field-level + message-level accuracy.

Usage (from the project root):
    python eval/run_eval.py                    # print report
    python eval/run_eval.py --write            # also append a run to eval/results.md
    python eval/run_eval.py --limit 5 --note "added number-word rule"
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from eval.scoring import score_sample  # noqa: E402
from extract import ExtractionError, extract  # noqa: E402
from prompts import PROMPT_VERSION  # noqa: E402

EVAL_TODAY = "2026-11-17"  # fixed so "kal"/"parso" samples have stable expected dates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=str(ROOT / "eval" / "samples.jsonl"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--write", action="store_true", help="append this run to eval/results.md")
    ap.add_argument("--note", default="", help="what changed since the last run")
    ap.add_argument("--delay", type=float, default=6.0, help="seconds between calls (free-tier rate limit)")
    args = ap.parse_args()

    samples = [json.loads(l) for l in Path(args.samples).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        samples = samples[: args.limit]

    hits = total = exact = 0
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # hits, total, exact, n
    failures: list[tuple] = []

    for i, s in enumerate(samples):
        if i:
            time.sleep(args.delay)
        try:
            pred = extract(s["message"], EVAL_TODAY)
        except ExtractionError as e:
            failures.append((s["id"], s["category"], s["message"], [f"EXTRACTION ERROR: {e}"]))
            n_fields = max(1, len(score_sample(s["expected"], __import__("schema").SiteUpdate()).fields))
            total += n_fields
            by_cat[s["category"]][1] += n_fields
            by_cat[s["category"]][3] += 1
            continue
        res = score_sample(s["expected"], pred)
        hits += res.hits
        total += res.total
        exact += res.exact
        c = by_cat[s["category"]]
        c[0] += res.hits; c[1] += res.total; c[2] += res.exact; c[3] += 1
        status = "ok  " if res.exact else "FAIL"
        print(f"[{status}] #{s['id']:>2} {s['category']:<10} {s['message'][:70]}")
        if not res.exact:
            failures.append((s["id"], s["category"], s["message"], res.failures()))

    n = len(samples)
    lines = [
        f"Model: {config.MODEL} | prompt {PROMPT_VERSION} | {n} samples",
        f"Field-level accuracy:  {hits}/{total} = {100 * hits / max(total, 1):.1f}%",
        f"Message-level exact:   {exact}/{n} = {100 * exact / max(n, 1):.1f}%",
        "",
        "| Category | Samples | Field acc | Exact |",
        "|---|---|---|---|",
    ]
    for cat, (h, t, e, k) in by_cat.items():
        lines.append(f"| {cat} | {k} | {100 * h / max(t, 1):.0f}% ({h}/{t}) | {e}/{k} |")
    lines += ["", "Failures:", "| # | Category | Message | What went wrong | Cause |", "|---|---|---|---|---|"]
    for sid, cat, msg, why in failures:
        lines.append(f"| {sid} | {cat} | {msg} | {'; '.join(why)} | _fill in_ |")
    report = "\n".join(lines)
    print("\n" + report)

    if args.write:
        out = ROOT / "eval" / "results.md"
        with out.open("a", encoding="utf-8") as f:
            f.write(f"\n\n## Run {date.today().isoformat()} - prompt {PROMPT_VERSION}\n")
            if args.note:
                f.write(f"Change: {args.note}\n")
            f.write("\n" + report + "\n")
        print(f"\nAppended to {out}")


if __name__ == "__main__":
    main()
