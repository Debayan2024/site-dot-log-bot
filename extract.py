"""Gemini call -> validated SiteUpdate, using structured (JSON-schema) output."""
import logging
import time

from google import genai
from google.genai import errors, types

import config
from prompts import SYSTEM_PROMPT
from schema import SiteUpdate

log = logging.getLogger(__name__)

_client: genai.Client | None = None


class ExtractionError(Exception):
    """Raised when the model output could not be obtained or validated."""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY or None,
                               http_options=types.HttpOptions(timeout=30000))  # never hang the bot
    return _client


def extract(message: str, today: str, retries: int = 3) -> SiteUpdate:
    """Extract a SiteUpdate from a message. Retries (with a wait on rate limits), then raises ExtractionError."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _get_client().models.generate_content(
                model=config.MODEL,
                contents=message,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT.format(today=today),
                    response_mime_type="application/json",
                    response_schema=SiteUpdate,
                    temperature=0,
                ),
            )
            if not resp.text:
                raise ValueError("model returned an empty response")
            return SiteUpdate.model_validate_json(resp.text)  # pydantic ValidationError is a ValueError
        except errors.APIError as e:
            last = e
            log.warning("extract attempt %d failed: %s", attempt + 1, e)
            if getattr(e, "code", None) in (429, 500, 503, 504) and attempt < retries:
                time.sleep(5 * (attempt + 1) if e.code != 429 else 20)  # busy server / free-tier rate limit
        except ValueError as e:
            last = e
            log.warning("extract attempt %d failed: %s", attempt + 1, e)
    raise ExtractionError(str(last))


if __name__ == "__main__":
    # Quick manual test: python extract.py "aaj 40 sqft ceiling, 6 mazdoor"
    import sys
    from datetime import datetime

    msg = " ".join(sys.argv[1:]) or "Aaj 40 sq ft false ceiling ka kaam hua, 6 mazdoor, 2 bag cement nikala"
    print(extract(msg, datetime.now(config.TZ).date().isoformat()).model_dump_json(indent=2))
