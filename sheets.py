"""Sync rows to Google Sheets (gspread) or fall back to CSV export. SQLite stays the truth."""
import csv
import logging
import os

import config
import db

log = logging.getLogger(__name__)

PROGRESS_TAB = "Daily Progress"
STOCK_TAB = "Stock Ledger"
BALANCE_TAB = "Stock Balance"

PROGRESS_HEADER = ["Date", "Task", "Quantity", "Unit", "Labour", "Remarks", "Reported by", "Entry ID"]
STOCK_HEADER = ["Date", "Material", "Qty", "Unit", "In/Out", "Note", "Reported by", "Entry ID"]
ENTRY_ID_COL = 8  # both tabs


def _progress_row(r: dict) -> list:
    return [r["report_date"], r["task"], r["quantity"], r["unit"], r["labour_count"],
            r["remarks"], r["reported_by"], r["entry_id"]]


def _stock_row(r: dict) -> list:
    return [r["report_date"], r["material"], r["quantity"], r["unit"], r["direction"],
            r["note"], r["reported_by"], r["entry_id"]]


def is_configured() -> bool:
    return bool(config.GOOGLE_SHEET_ID and os.path.exists(config.GOOGLE_CREDENTIALS_FILE))


def _open_sheet():
    import gspread
    gc = gspread.service_account(filename=config.GOOGLE_CREDENTIALS_FILE)
    return gc.open_by_key(config.GOOGLE_SHEET_ID)


def _get_or_create(sh, title: str, header: list[str]):
    import gspread
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(header))
        ws.append_row(header)
        return ws


def _ensure_balance_tab(sh) -> None:
    import gspread
    try:
        sh.worksheet(BALANCE_TAB)
        return
    except gspread.WorksheetNotFound:
        pass
    ws = sh.add_worksheet(title=BALANCE_TAB, rows=200, cols=4)
    ws.update(range_name="A1:C1", values=[["Material", "Unit", "Balance (in - out)"]])
    led = f"'{STOCK_TAB}'"
    # one row per material; units are NOT converted, so mixed units show a wrong total - check the Unit column
    ws.update(range_name="A2", values=[[f"=SORT(UNIQUE(FILTER({led}!B2:B, {led}!B2:B<>\"\")))"]],
              value_input_option="USER_ENTERED")
    ws.update(range_name="B2", values=[[
        f"=ARRAYFORMULA(IF(A2:A=\"\",\"\",IFERROR(VLOOKUP(A2:A,{led}!B2:D,3,FALSE),\"\")))"]],
        value_input_option="USER_ENTERED")
    ws.update(range_name="C2", values=[[
        f"=ARRAYFORMULA(IF(A2:A=\"\",\"\",SUMIFS({led}!C2:C,{led}!B2:B,A2:A,{led}!E2:E,\"received\")"
        f"-SUMIFS({led}!C2:C,{led}!B2:B,A2:A,{led}!E2:E,\"issued\")))"]],
        value_input_option="USER_ENTERED")


def sync_pending() -> str:
    """Push unsynced rows. Raises on failure (caller logs; rows stay unsynced and retry next time)."""
    if not is_configured():
        return export_csv()
    p_rows, s_rows = db.unsynced_rows()
    if not p_rows and not s_rows:
        return "nothing to sync"
    sh = _open_sheet()
    _ensure_balance_tab(sh)
    if p_rows:
        ws = _get_or_create(sh, PROGRESS_TAB, PROGRESS_HEADER)
        ws.append_rows([_progress_row(r) for r in p_rows], value_input_option="USER_ENTERED")
        db.mark_synced("progress", [r["id"] for r in p_rows])
    if s_rows:
        ws = _get_or_create(sh, STOCK_TAB, STOCK_HEADER)
        ws.append_rows([_stock_row(r) for r in s_rows], value_input_option="USER_ENTERED")
        db.mark_synced("stock_moves", [r["id"] for r in s_rows])
    return f"synced {len(p_rows)} progress + {len(s_rows)} stock rows"


def remove_entry(entry_id: str) -> None:
    """Best-effort removal of an undone entry's rows from the Sheet (CSV fallback re-exports in full)."""
    if not is_configured():
        export_csv()
        return
    sh = _open_sheet()
    for title in (PROGRESS_TAB, STOCK_TAB):
        try:
            ws = sh.worksheet(title)
        except Exception:
            continue
        cells = ws.findall(entry_id, in_column=ENTRY_ID_COL)
        for row in sorted({c.row for c in cells}, reverse=True):
            ws.delete_rows(row)


def export_csv() -> str:
    """Zero-setup fallback: write two CSVs the engineer can open in Excel."""
    os.makedirs(config.EXPORT_DIR, exist_ok=True)
    p_rows, s_rows = db.all_rows()
    with open(os.path.join(config.EXPORT_DIR, "daily_progress.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(PROGRESS_HEADER)
        w.writerows(_progress_row(r) for r in p_rows)
    with open(os.path.join(config.EXPORT_DIR, "stock_ledger.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(STOCK_HEADER)
        w.writerows(_stock_row(r) for r in s_rows)
    return f"exported CSVs to ./{config.EXPORT_DIR}"
