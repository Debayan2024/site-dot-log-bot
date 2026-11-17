# Site Daily Log Bot

A Telegram bot that turns plain Hindi / English / Hinglish site messages into a structured daily progress log and stock register, with confirm-before-save and an end-of-day summary for the site engineer.

> Status: prototype. Not yet tested with real site users. Eval numbers are in [eval/results.md](eval/results.md) (none recorded until you run it with your API key).

## Why this exists

Site teams already report on WhatsApp/Telegram and paper registers. Software is easy to build; getting a busy site team to use it every day is the hard part. So the design rule is: **the worker keeps writing the way they already talk, and the system does the structuring.**

| Who | What they do | What the bot gives them |
|---|---|---|
| Supervisor | Sends a one-line update in Hindi / English / Hinglish ("aaj 40 sqft ceiling, 6 mazdoor") | Read-back and one-tap confirm, no forms or typing rules |
| Store keeper | Sends material in / out ("20 bag cement aaya") | Live stock balance via `/stock cement` |
| Site engineer | Reviews the day | One end-of-day summary with anomaly flags, plus a Sheet that stays up to date |

Built to be low-friction: no app install beyond Telegram, no training on a format, and a wrong entry is fixed with `/undo` instead of a call to the office.

## Adoption plan (for a real site rollout)

This is the plan, not a result. None of it has been run on a live site yet.

1. **Week 1: shadow.** Sit with the site engineer, store keeper and supervisors. Watch how the paper register and WhatsApp reporting really work. Collect real message samples and add them to `eval/samples.jsonl`.
2. **Week 2: pilot.** Onboard one or two willing people. Check the eval and failure table against their real messages and fix the prompt and units first.
3. **Week 3 onward: expand.** Add the rest of the team. Whenever someone slips back to paper, find out why and fix the setup or report it to the tech team.
4. **Ongoing.** A daily check-in with the tech team and a weekly update. Track what is used, what is skipped and which fixes were made.

Things to set up per project: site name, `ALLOWED_USER_IDS`, `SUMMARY_CHAT_IDS`, `EXPECTED_REPORTERS`, and the material and task names the site actually uses.

## Architecture

```
Telegram message
   |
   v
allowed-user check (config.ALLOWED_USER_IDS)
   |
   v
extract.py  --Gemini structured output--> SiteUpdate (pydantic, schema.py)
   |                 \__ needs_clarification? -> bot asks, re-extracts with the answer
   v
read-back + [Yes] [Edit] [Cancel]
   |
   +-- Yes --> db.py (SQLite = source of truth) --> sheets.py (Google Sheet or CSV = a view)
   |
   +-- daily 17:30 job --> summary.py (summary + 4 anomaly rules) --> site engineer
```

**SQLite is the truth, the Sheet is a view.** If a sync fails nothing is lost; unsynced rows retry on the next save and at end of day.

## Setup

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows (use source .venv/bin/activate on Linux/Mac)
pip install -r requirements.txt
cp .env.example .env                                # then fill it in
python bot.py
```

1. Create a bot with @BotFather and put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Get a free Gemini key at https://aistudio.google.com/apikey and put it in `.env` as `GEMINI_API_KEY`.
3. Message the bot once. It replies "Not authorised (Your Telegram ID is ...)". Add that ID to `ALLOWED_USER_IDS` and restart.
4. Set `SUMMARY_CHAT_IDS` (engineer) and `EXPECTED_REPORTERS` (names who must report daily).
5. Default model is `gemini-3.5-flash-lite` (fast; the larger flash models were overloaded/timing out when tested). If it is retired, set `GEMINI_MODEL` to a current Flash model (https://ai.google.dev/gemini-api/docs/models).

The extractor is the only provider-specific part (`extract.py`); swapping LLMs means rewriting that one function.

Try the extractor without Telegram: `python extract.py "aaj 40 sqft ceiling, 6 mazdoor"`

### Google Sheets (optional)
Create a service account, download its JSON as `credentials.json`, share your Sheet with the service account's email, and set `GOOGLE_SHEET_ID`. Tabs `Daily Progress`, `Stock Ledger` and `Stock Balance` are created automatically. Without it, `sheets.py` writes `exports/daily_progress.csv` and `exports/stock_ledger.csv` instead.

## Commands

| Command | Does |
|---|---|
| `/start` | Help in Hindi + English with a copyable example |
| `/today` | Today's log for the whole site |
| `/stock cement` | Balance from SQLite (exact lookup, no LLM) |
| `/undo` | Deletes your last entry (also from the Sheet) |
| `/summary` | The end-of-day summary, right now |
| `/cancel` | Discards a pending message or clarification |

## Anomaly rules (daily summary)

1. Stock balance below zero (and mixed units for one material are flagged, never silently converted)
2. Expected reporter sent nothing today
3. Quantity more than 3x that task's previous maximum (likely typo, e.g. 400 instead of 40)
4. Same entry logged twice by the same person within 5 minutes

## Evaluation

33 invented, hand-labelled messages in [eval/samples.jsonl](eval/samples.jsonl) (English, Hinglish, Devanagari, multi-item, messy, vague, direction confusion, dates). Expected outputs were written before running the model.

```bash
python eval/run_eval.py --write --note "baseline v1"
```

Reports field-level accuracy, message-level exact match, per-category accuracy, and a failure table. Record causes and prompt changes in `eval/results.md`.

## Tests

```bash
python -m pytest
```
Covers stock maths, undo, labour double-counting, the four anomaly rules, and the scorer.

## Known limitations

- Text only. Voice notes, challan photos and `/ask` over site PDFs (reuse the RAG project) are not built yet.
- Pending confirmations live in memory (`user_data`); a bot restart expires them and the user must resend.
- Units are never converted (bags vs kg), only flagged.
- Undoing an entry that was already synced removes its Sheet rows best-effort.
- Sheet `Stock Balance` tab sums quantities per material regardless of unit; check the Unit column when units are mixed.
- No real-site feedback yet.
- Kannada messages are not covered by the eval set (English, Hinglish and Devanagari only).

## Roadmap (driven by what site users ask for)

- Voice notes, since many supervisors prefer speaking to typing.
- Challan photo capture for the store keeper.
- Persist pending confirmations so a restart does not lose them.
- Kannada support and local material names.
- `/ask` over site drawings and PDFs.

## Hours

Started: ____  Finished: ____  Total: ____ h (fill in honestly)
