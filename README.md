# WhatsApp Expense Bot

Personal expense tracker used from WhatsApp. The owner texts the bot in English,
Urdu or Roman Urdu; an LLM (via OpenRouter) picks tools, and those tools call pure
Python services backed by SQLAlchemy. The LLM never computes numbers — every total
and comparison comes from a service.

## Features

- Log expenses and income from text or voice notes
- Accounts with balances, categories, budgets
- Debts (lent / borrowed / repaid) and savings goals
- Analytics: totals, breakdowns, period comparisons
- PDF reports and a calendar spending view

## Stack

Python 3.12, FastAPI, SQLAlchemy 2.0, pydantic-settings, httpx, the OpenAI SDK
pointed at OpenRouter, ReportLab, ffmpeg, pytest. SQLite locally, Postgres in
production.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # then fill in the values
```

`ffmpeg` must be on `PATH` for voice note transcription.

## Running

```bash
uvicorn app.main:app --reload --port 8000
```

| Command | Purpose |
| --- | --- |
| `pytest -q` | Run the test suite |
| `python -m scripts.chat_cli` | Chat with the bot locally, without WhatsApp |
| `python -m scripts.send_test_message` | Send a test message through the Cloud API |

## Layout

```
app/
  main.py          FastAPI app, lifespan, /health, /privacy
  config.py        Settings
  db.py            Engine, session scope, init_db
  models.py        ORM models
  bootstrap.py     Idempotent seeding
  handler.py       handle_message()
  utils/           dates, money helpers
  services/        pure business logic
  whatsapp/        Cloud API client, payload parsing, webhook
  llm/             client, prompts, tools, dispatcher, agent
  voice/           transcription
  reports/         PDF and calendar views
scripts/           developer helpers
tests/             pytest suite
```

## Configuration

All configuration comes from environment variables; see `.env.example` for the
full list with defaults.
