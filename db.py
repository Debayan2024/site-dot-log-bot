"""SQLite: the source of truth. The Google Sheet is only a view of this."""
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Optional

import config
from schema import SiteUpdate

DB_PATH = config.DB_PATH  # tests override this

_TS = "strftime('%Y-%m-%d %H:%M:%f','now')"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS progress (
  id INTEGER PRIMARY KEY,
  entry_id TEXT NOT NULL,
  report_date TEXT NOT NULL,
  task TEXT NOT NULL,
  quantity REAL,
  unit TEXT,
  labour_count INTEGER,
  remarks TEXT,
  reported_by TEXT,
  reporter_id TEXT,
  raw_message TEXT,
  synced INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT ({_TS})
);
CREATE TABLE IF NOT EXISTS stock_moves (
  id INTEGER PRIMARY KEY,
  entry_id TEXT NOT NULL,
  report_date TEXT NOT NULL,
  material TEXT NOT NULL,
  quantity REAL NOT NULL,
  unit TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('issued','received')),
  note TEXT,
  reported_by TEXT,
  reporter_id TEXT,
  raw_message TEXT,
  synced INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT ({_TS})
);
CREATE INDEX IF NOT EXISTS idx_progress_date ON progress(report_date);
CREATE INDEX IF NOT EXISTS idx_stock_material ON stock_moves(material);
"""

NO_TASK = "(no task stated)"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- writes

def insert_update(
    update: SiteUpdate,
    report_date: str,
    reported_by: str,
    reporter_id: str,
    raw_message: str,
    created_at: Optional[str] = None,
) -> str:
    """Insert one confirmed message. Returns an entry_id grouping all its rows."""
    entry_id = uuid.uuid4().hex[:12]
    with get_conn() as c:
        # labour_count / remarks live on the first progress row only, so SUM() never double counts
        rows = [(w.task, w.quantity, w.unit) for w in update.work_items]
        if not rows and (update.labour_count is not None or update.remarks):
            rows = [(NO_TASK, None, None)]
        for i, (task, qty, unit) in enumerate(rows):
            c.execute(
                """INSERT INTO progress (entry_id, report_date, task, quantity, unit, labour_count,
                   remarks, reported_by, reporter_id, raw_message, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, COALESCE(?, %s))""" % _TS,
                (entry_id, report_date, task, qty, unit,
                 update.labour_count if i == 0 else None,
                 update.remarks if i == 0 else None,
                 reported_by, reporter_id, raw_message, created_at),
            )
        for m in update.materials:
            c.execute(
                """INSERT INTO stock_moves (entry_id, report_date, material, quantity, unit, direction,
                   note, reported_by, reporter_id, raw_message, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, COALESCE(?, %s))""" % _TS,
                (entry_id, report_date, m.material, m.quantity, m.unit, m.direction, m.note,
                 reported_by, reporter_id, raw_message, created_at),
            )
    return entry_id


def delete_last_entry(reporter_id: str) -> Optional[dict]:
    """Delete the most recent entry by this reporter. Returns info about what was removed."""
    with get_conn() as c:
        row = c.execute(
            """SELECT entry_id FROM (
                 SELECT entry_id, created_at FROM progress WHERE reporter_id = ?
                 UNION ALL
                 SELECT entry_id, created_at FROM stock_moves WHERE reporter_id = ?
               ) ORDER BY created_at DESC LIMIT 1""",
            (reporter_id, reporter_id),
        ).fetchone()
        if row is None:
            return None
        eid = row["entry_id"]
        prog = [dict(r) for r in c.execute("SELECT * FROM progress WHERE entry_id = ?", (eid,))]
        stock = [dict(r) for r in c.execute("SELECT * FROM stock_moves WHERE entry_id = ?", (eid,))]
        c.execute("DELETE FROM progress WHERE entry_id = ?", (eid,))
        c.execute("DELETE FROM stock_moves WHERE entry_id = ?", (eid,))
    return {
        "entry_id": eid,
        "progress": prog,
        "stock": stock,
        "was_synced": any(r["synced"] for r in prog + stock),
    }


# ---------------------------------------------------------------- sync helpers

def unsynced_rows() -> tuple[list[dict], list[dict]]:
    with get_conn() as c:
        p = [dict(r) for r in c.execute("SELECT * FROM progress WHERE synced = 0 ORDER BY id")]
        s = [dict(r) for r in c.execute("SELECT * FROM stock_moves WHERE synced = 0 ORDER BY id")]
    return p, s


def all_rows() -> tuple[list[dict], list[dict]]:
    with get_conn() as c:
        p = [dict(r) for r in c.execute("SELECT * FROM progress ORDER BY report_date, id")]
        s = [dict(r) for r in c.execute("SELECT * FROM stock_moves ORDER BY report_date, id")]
    return p, s


def mark_synced(table: str, ids: list[int]) -> None:
    assert table in ("progress", "stock_moves")
    if not ids:
        return
    with get_conn() as c:
        c.executemany(f"UPDATE {table} SET synced = 1 WHERE id = ?", [(i,) for i in ids])


# ---------------------------------------------------------------- reads

def progress_on(report_date: str) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM progress WHERE report_date = ? ORDER BY id", (report_date,))]


def stock_on(report_date: str) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM stock_moves WHERE report_date = ? ORDER BY id", (report_date,))]


def stock_balance(material: Optional[str] = None) -> list[dict]:
    """balance = SUM(received) - SUM(issued), per (material, unit). Units are never converted."""
    sql = """SELECT lower(material) AS material, unit,
               SUM(CASE direction WHEN 'received' THEN quantity ELSE 0 END) AS received,
               SUM(CASE direction WHEN 'issued'   THEN quantity ELSE 0 END) AS issued,
               SUM(CASE direction WHEN 'received' THEN quantity ELSE -quantity END) AS balance
             FROM stock_moves"""
    args: tuple = ()
    if material:
        sql += " WHERE lower(material) LIKE ?"
        args = (f"%{material.strip().lower()}%",)
    sql += " GROUP BY lower(material), unit ORDER BY material, unit"
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


# ---------------------------------------------------------------- anomaly queries

def negative_balances() -> list[dict]:
    return [r for r in stock_balance() if r["balance"] < 0]


def mixed_unit_materials() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in stock_balance():
        out.setdefault(r["material"], []).append(r["unit"])
    return {m: u for m, u in out.items() if len(u) > 1}


def reporters_on(report_date: str) -> list[str]:
    with get_conn() as c:
        rows = c.execute(
            """SELECT DISTINCT reported_by FROM (
                 SELECT reported_by, report_date FROM progress
                 UNION SELECT reported_by, report_date FROM stock_moves
               ) WHERE report_date = ? AND reported_by IS NOT NULL ORDER BY reported_by""",
            (report_date,),
        ).fetchall()
    return [r["reported_by"] for r in rows]


def outlier_quantities(report_date: str, factor: float = config.OUTLIER_FACTOR) -> list[dict]:
    """Quantities on `report_date` more than `factor` x the previous maximum for the same task/unit."""
    with get_conn() as c:
        work = c.execute(
            """SELECT p.task AS name, p.unit, p.quantity, m.mx AS previous_max
               FROM progress p JOIN (
                 SELECT lower(task) t, unit, MAX(quantity) mx FROM progress
                 WHERE report_date < ? AND quantity IS NOT NULL GROUP BY lower(task), unit
               ) m ON lower(p.task) = m.t AND p.unit IS m.unit
               WHERE p.report_date = ? AND p.quantity > ? * m.mx""",
            (report_date, report_date, factor),
        ).fetchall()
        mats = c.execute(
            """SELECT s.material AS name, s.unit, s.quantity, m.mx AS previous_max
               FROM stock_moves s JOIN (
                 SELECT lower(material) t, unit, direction, MAX(quantity) mx FROM stock_moves
                 WHERE report_date < ? GROUP BY lower(material), unit, direction
               ) m ON lower(s.material) = m.t AND s.unit = m.unit AND s.direction = m.direction
               WHERE s.report_date = ? AND s.quantity > ? * m.mx""",
            (report_date, report_date, factor),
        ).fetchall()
    return [dict(r) for r in list(work) + list(mats)]


def duplicate_entries(report_date: str, window_min: int = config.DUPLICATE_WINDOW_MIN) -> list[dict]:
    """Same item logged twice by the same reporter within `window_min` minutes."""
    with get_conn() as c:
        work = c.execute(
            """SELECT a.task AS name, a.quantity, a.unit, a.reported_by FROM progress a
               JOIN progress b ON a.id < b.id AND a.entry_id != b.entry_id
                AND lower(a.task) = lower(b.task) AND a.quantity IS b.quantity AND a.unit IS b.unit
                AND a.reported_by IS b.reported_by AND a.report_date = b.report_date
                AND abs(julianday(a.created_at) - julianday(b.created_at)) * 1440 <= ?
               WHERE a.report_date = ? AND a.task != ?""",
            (window_min, report_date, NO_TASK),
        ).fetchall()
        mats = c.execute(
            """SELECT a.material AS name, a.quantity, a.unit, a.reported_by FROM stock_moves a
               JOIN stock_moves b ON a.id < b.id AND a.entry_id != b.entry_id
                AND lower(a.material) = lower(b.material) AND a.quantity = b.quantity
                AND a.unit = b.unit AND a.direction = b.direction
                AND a.reported_by IS b.reported_by AND a.report_date = b.report_date
                AND abs(julianday(a.created_at) - julianday(b.created_at)) * 1440 <= ?
               WHERE a.report_date = ?""",
            (window_min, report_date),
        ).fetchall()
    return [dict(r) for r in list(work) + list(mats)]
