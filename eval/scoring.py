"""Field-level scoring of a predicted SiteUpdate against a hand-written expected dict.

Names (task / material) match loosely: lowercase, punctuation stripped, substring either way,
and an expected name may be a list of acceptable variants. Numbers and units match exactly after
removing case/punctuation/spaces ("sq ft" == "sqft" == "sq.ft"), so unit normalisation by the model
is still tested for real differences like "mtr" vs "m".
"""
import re
from dataclasses import dataclass, field

from schema import SiteUpdate


def norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def name_match(expected, predicted: str) -> bool:
    variants = expected if isinstance(expected, list) else [expected]
    p = norm(predicted)
    return any(norm(v) and p and (norm(v) in p or p in norm(v)) for v in variants)


def _num_eq(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


@dataclass
class Result:
    fields: list = field(default_factory=list)   # (label, ok, detail)
    extras: list = field(default_factory=list)   # hallucinated / unexpected content

    @property
    def hits(self) -> int:
        return sum(ok for _, ok, _ in self.fields)

    @property
    def total(self) -> int:
        return len(self.fields)

    @property
    def exact(self) -> bool:
        return self.hits == self.total and not self.extras

    def failures(self) -> list[str]:
        out = [f"{label}: {detail}" for label, ok, detail in self.fields if not ok]
        out += [f"EXTRA {e}" for e in self.extras]
        return out


def _pair(expected_items, predicted_items, name_key, pass1=None):
    """Greedy pairing of expected -> predicted. pass1 is a stricter predicate tried first."""
    used: set[int] = set()
    pairs: list = [None] * len(expected_items)
    for strict in ([True] if pass1 else []) + [False]:
        for i, e in enumerate(expected_items):
            if pairs[i] is not None:
                continue
            for j, p in enumerate(predicted_items):
                if j in used or not name_match(e[name_key], getattr(p, name_key)):
                    continue
                if strict and not pass1(e, p):
                    continue
                pairs[i] = j
                used.add(j)
                break
    extras = [p for j, p in enumerate(predicted_items) if j not in used]
    return pairs, extras


def score_sample(expected: dict, pred: SiteUpdate) -> Result:
    r = Result()

    if expected.get("needs_clarification"):
        r.fields.append(("asked_clarification", bool(pred.needs_clarification), pred.needs_clarification))
        guessed = bool(pred.work_items or pred.materials)
        r.fields.append(("did_not_guess", not guessed, "recorded items instead of asking" if guessed else ""))
        return r

    if pred.needs_clarification:
        r.fields.append(("no_needless_clarification", False, f"asked: {pred.needs_clarification}"))

    # work items
    exp_w = expected.get("work_items", [])
    pairs, extra_w = _pair(exp_w, pred.work_items, "task")
    for e, j in zip(exp_w, pairs):
        label = e["task"] if isinstance(e["task"], str) else e["task"][0]
        p = pred.work_items[j] if j is not None else None
        r.fields.append((f"work[{label}].found", p is not None, "missing"))
        if p is None:
            r.fields.append((f"work[{label}].quantity", False, "missing"))
            r.fields.append((f"work[{label}].unit", False, "missing"))
            continue
        r.fields.append((f"work[{label}].quantity", _num_eq(e.get("quantity"), p.quantity),
                         f"expected {e.get('quantity')}, got {p.quantity}"))
        r.fields.append((f"work[{label}].unit", norm(e.get("unit")) == norm(p.unit),
                         f"expected {e.get('unit')!r}, got {p.unit!r}"))
    r.extras += [f"work item {p.task} {p.quantity} {p.unit}" for p in extra_w]

    # materials: pair on name+direction first, then name only (so direction errors score as direction errors)
    exp_m = expected.get("materials", [])
    pairs, extra_m = _pair(exp_m, pred.materials, "material",
                           pass1=lambda e, p: e["direction"] == p.direction)
    for e, j in zip(exp_m, pairs):
        label = e["material"] if isinstance(e["material"], str) else e["material"][0]
        p = pred.materials[j] if j is not None else None
        r.fields.append((f"mat[{label}].found", p is not None, "missing"))
        if p is None:
            for k in ("quantity", "unit", "direction"):
                r.fields.append((f"mat[{label}].{k}", False, "missing"))
            continue
        r.fields.append((f"mat[{label}].quantity", _num_eq(e["quantity"], p.quantity),
                         f"expected {e['quantity']}, got {p.quantity}"))
        r.fields.append((f"mat[{label}].unit", norm(e["unit"]) == norm(p.unit),
                         f"expected {e['unit']!r}, got {p.unit!r}"))
        r.fields.append((f"mat[{label}].direction", e["direction"] == p.direction,
                         f"expected {e['direction']}, got {p.direction}"))
    r.extras += [f"material {p.material} {p.quantity} {p.unit} {p.direction}" for p in extra_m]

    # labour: score if expected; if not expected, a predicted count is a hallucination
    if "labour_count" in expected:
        r.fields.append(("labour_count", pred.labour_count == expected["labour_count"],
                         f"expected {expected['labour_count']}, got {pred.labour_count}"))
    elif pred.labour_count is not None:
        r.extras.append(f"labour_count {pred.labour_count}")

    if "report_date" in expected:
        got = pred.report_date.isoformat() if pred.report_date else None
        r.fields.append(("report_date", got == expected["report_date"],
                         f"expected {expected['report_date']}, got {got}"))

    if expected.get("remarks") is True:
        r.fields.append(("remarks", bool(pred.remarks), "no remarks captured"))

    return r
