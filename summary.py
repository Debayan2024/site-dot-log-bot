"""Read-back text, daily summary and anomaly checks. Pure functions over db.py; no Telegram here."""
from collections import OrderedDict
from datetime import date

import config
import db
from schema import SiteUpdate


def fmt_qty(q) -> str:
    if q is None:
        return ""
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def fmt_date(d: str) -> str:
    return date.fromisoformat(d).strftime("%d %b %Y").lstrip("0")


def format_update(update: SiteUpdate, report_date: str) -> str:
    """The plain-language read-back shown before the user confirms."""
    lines = []
    for w in update.work_items:
        lines.append(" ".join(x for x in (fmt_qty(w.quantity), w.unit, w.task) if x))
    if update.labour_count is not None:
        lines.append(f"{update.labour_count} workers")
    for m in update.materials:
        note = f" ({m.note})" if m.note else ""
        lines.append(f"{m.material.capitalize() if m.material.islower() else m.material}: "
                     f"{fmt_qty(m.quantity)} {m.unit} {m.direction}{note}")
    if update.remarks:
        lines.append(f"Remarks: {update.remarks}")
    lines.append(f"Date: {fmt_date(report_date)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- anomalies

def anomalies(report_date: str) -> list[str]:
    flags: list[str] = []

    # 1. stock balance below zero
    for r in db.negative_balances():
        flags.append(f"{r['material'].capitalize()} balance is {fmt_qty(r['balance'])} {r['unit']} "
                     f"(issued more than received)")

    # unit consistency: flag, never silently convert
    for material, units in db.mixed_unit_materials().items():
        flags.append(f"{material.capitalize()} is logged in mixed units ({', '.join(units)}); "
                     f"balances are shown separately, not converted")

    # 2. expected reporter sent nothing
    got = [n.lower() for n in db.reporters_on(report_date)]
    missing = [e for e in config.EXPECTED_REPORTERS if not any(e.lower() in g for g in got)]
    if missing:
        flags.append(f"No report from: {', '.join(missing)}")

    # 3. quantity far outside normal
    for o in db.outlier_quantities(report_date):
        flags.append(f"Check: {o['name']} {fmt_qty(o['quantity'])} {o['unit'] or ''} is more than "
                     f"{config.OUTLIER_FACTOR:g}x the previous max ({fmt_qty(o['previous_max'])}) - typo?")

    # 4. duplicates
    seen = set()
    for d in db.duplicate_entries(report_date):
        key = (d["name"].lower(), d["quantity"], d["unit"], d["reported_by"])
        if key in seen:
            continue
        seen.add(key)
        flags.append(f"Possible duplicate: {d['name']} {fmt_qty(d['quantity'])} {d['unit'] or ''} "
                     f"logged twice by {d['reported_by']} within {config.DUPLICATE_WINDOW_MIN} min")
    return flags


# ---------------------------------------------------------------- summary

def build_summary(report_date: str, with_flags: bool = True) -> str:
    progress = db.progress_on(report_date)
    stock = db.stock_on(report_date)
    out = [f"Site Daily Log, {fmt_date(report_date)}"]

    if not progress and not stock:
        out.append("Nothing logged yet.")
    else:
        work: "OrderedDict[tuple, float | None]" = OrderedDict()
        for r in progress:
            if r["task"] == db.NO_TASK:
                continue
            key = (r["task"].lower(), r["unit"])
            work.setdefault(key, None)
            if r["quantity"] is not None:
                work[key] = (work[key] or 0) + r["quantity"]
        if work:
            out.append("Work done:")
            for (task, unit), q in work.items():
                qty = f"{fmt_qty(q)} {unit or ''}".strip() if q is not None else "(no qty)"
                out.append(f"- {task.capitalize()}: {qty}")

        labour = [(r["reported_by"], r["labour_count"]) for r in progress if r["labour_count"] is not None]
        if labour:
            total = sum(n for _, n in labour)
            parts = " + ".join(str(n) for _, n in labour)
            out.append(f"Labour: {total} total" + (f" ({parts})" if len(labour) > 1 else ""))

        for direction, label in (("received", "Materials in"), ("issued", "Materials out")):
            agg: "OrderedDict[tuple, float]" = OrderedDict()
            for r in stock:
                if r["direction"] == direction:
                    key = (r["material"].lower(), r["unit"])
                    agg[key] = agg.get(key, 0) + r["quantity"]
            if agg:
                out.append(f"{label}: " + ", ".join(f"{m} {fmt_qty(q)} {u}" for (m, u), q in agg.items()))

        remarks = [f"{r['remarks']} ({r['reported_by']})" for r in progress if r["remarks"]]
        if remarks:
            out.append("Remarks: " + "; ".join(remarks))

        reporters = db.reporters_on(report_date)
        if reporters:
            out.append("Reports received from: " + ", ".join(reporters))

    if with_flags:
        flags = anomalies(report_date)
        if flags:
            out.append("Flags:")
            out.extend(f"- {f}" for f in flags)
    return "\n".join(out)


def format_stock(material: str) -> str:
    rows = db.stock_balance(material)
    if not rows:
        return f"No stock records for '{material}'."
    lines = []
    for r in rows:
        warn = " ⚠️" if r["balance"] < 0 else ""
        lines.append(f"{r['material'].capitalize()}: {fmt_qty(r['balance'])} {r['unit']}{warn} "
                     f"(in {fmt_qty(r['received'])}, out {fmt_qty(r['issued'])})")
    return "\n".join(lines)
