import pytest

import db
import summary
from schema import MaterialMove, SiteUpdate, WorkItem


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()


def mat(material, qty, unit, direction, note=None):
    return MaterialMove(material=material, quantity=qty, unit=unit, direction=direction, note=note)


def add(update, date="2026-11-17", who="Ramesh", rid="1", created_at=None):
    return db.insert_update(update, date, who, rid, "raw", created_at=created_at)


def test_balance_is_received_minus_issued():
    add(SiteUpdate(materials=[mat("cement", 10, "bags", "received")]))
    add(SiteUpdate(materials=[mat("Cement", 4, "bags", "issued")]))
    (row,) = db.stock_balance("cement")
    assert row["balance"] == 6 and row["received"] == 10 and row["issued"] == 4


def test_negative_balance_flagged():
    add(SiteUpdate(materials=[mat("cement", 2, "bags", "issued")]))
    assert "balance is -2" in " ".join(summary.anomalies("2026-11-17"))


def test_mixed_units_not_converted_but_flagged():
    add(SiteUpdate(materials=[mat("cement", 10, "bags", "received"), mat("cement", 50, "kg", "issued")]))
    assert len(db.stock_balance("cement")) == 2
    assert any("mixed units" in f for f in summary.anomalies("2026-11-17"))


def test_labour_not_double_counted_across_work_items():
    add(SiteUpdate(work_items=[WorkItem(task="a", quantity=1, unit="m"), WorkItem(task="b", quantity=2, unit="m")],
                   labour_count=6))
    assert sum(r["labour_count"] or 0 for r in db.progress_on("2026-11-17")) == 6


def test_labour_only_message_is_kept():
    add(SiteUpdate(labour_count=9))
    assert db.progress_on("2026-11-17")[0]["labour_count"] == 9


def test_undo_removes_whole_last_entry_of_that_user_only():
    add(SiteUpdate(materials=[mat("sand", 1, "bags", "received")]), who="Suresh", rid="2")
    add(SiteUpdate(work_items=[WorkItem(task="ceiling", quantity=5, unit="m")],
                   materials=[mat("cement", 1, "bags", "issued")]), rid="1")
    removed = db.delete_last_entry("1")
    assert len(removed["progress"]) == 1 and len(removed["stock"]) == 1
    assert db.delete_last_entry("1") is None
    assert len(db.stock_balance("sand")) == 1  # other user's entry untouched


def test_outlier_typo_flagged():
    add(SiteUpdate(work_items=[WorkItem(task="false ceiling", quantity=40, unit="sq ft")]), date="2026-11-16")
    add(SiteUpdate(work_items=[WorkItem(task="False Ceiling", quantity=400, unit="sq ft")]))
    assert any("typo" in f for f in summary.anomalies("2026-11-17"))


def test_normal_quantity_not_flagged():
    add(SiteUpdate(work_items=[WorkItem(task="false ceiling", quantity=40, unit="sq ft")]), date="2026-11-16")
    add(SiteUpdate(work_items=[WorkItem(task="false ceiling", quantity=60, unit="sq ft")]))
    assert not any("typo" in f for f in summary.anomalies("2026-11-17"))


def test_duplicate_within_window_flagged_but_not_when_far_apart():
    u = SiteUpdate(work_items=[WorkItem(task="partition", quantity=20, unit="sq ft")])
    add(u, created_at="2026-11-17 10:00:00.000")
    add(u, created_at="2026-11-17 10:02:00.000")
    assert any("duplicate" in f.lower() for f in summary.anomalies("2026-11-17"))


def test_not_duplicate_when_far_apart():
    u = SiteUpdate(work_items=[WorkItem(task="partition", quantity=20, unit="sq ft")])
    add(u, created_at="2026-11-17 10:00:00.000")
    add(u, created_at="2026-11-17 14:00:00.000")
    assert not any("duplicate" in f.lower() for f in summary.anomalies("2026-11-17"))


def test_missing_expected_reporter(monkeypatch):
    monkeypatch.setattr(summary.config, "EXPECTED_REPORTERS", ["Ramesh", "Mahesh"])
    add(SiteUpdate(labour_count=3), who="Ramesh Kumar")
    flags = summary.anomalies("2026-11-17")
    assert any("No report from: Mahesh" in f for f in flags)


def test_summary_contents_and_unsynced_tracking():
    add(SiteUpdate(work_items=[WorkItem(task="false ceiling", quantity=40, unit="sq ft")], labour_count=6,
                   materials=[mat("cement", 2, "bags", "issued")]))
    text = summary.build_summary("2026-11-17")
    assert "False ceiling: 40 sq ft" in text and "Labour: 6 total" in text and "cement 2 bags" in text
    p, s = db.unsynced_rows()
    assert len(p) == 1 and len(s) == 1
    db.mark_synced("progress", [r["id"] for r in p])
    assert db.unsynced_rows()[0] == []
