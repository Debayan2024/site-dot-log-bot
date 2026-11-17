"""Loads environment variables and constants."""
import os
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

ALLOWED_USER_IDS = {int(x) for x in _csv("ALLOWED_USER_IDS")}
SUMMARY_CHAT_IDS = [int(x) for x in _csv("SUMMARY_CHAT_IDS")]
EXPECTED_REPORTERS = _csv("EXPECTED_REPORTERS")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
_h, _m = (os.getenv("SUMMARY_TIME", "17:30").split(":") + ["0"])[:2]
SUMMARY_TIME = time(int(_h), int(_m), tzinfo=TZ)

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
DB_PATH = os.getenv("DB_PATH", "site_log.db")
EXPORT_DIR = "exports"

# Anomaly thresholds
OUTLIER_FACTOR = 3.0        # flag if quantity > 3x the task's previous maximum
DUPLICATE_WINDOW_MIN = 5    # same entry twice within N minutes
