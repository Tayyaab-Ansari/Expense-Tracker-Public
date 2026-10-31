
````markdown
# WhatsApp Expense Bot — Project Rules

Personal expense tracker used from WhatsApp. Owner texts the bot (English / Urdu / Roman Urdu); an LLM (OpenRouter) picks tools; tools call pure Python services backed by SQLAlchemy.

## Stack
Python 3.12, FastAPI, SQLAlchemy 2.0 (typed `Mapped[]`), pydantic-settings, httpx (async), openai SDK pointed at OpenRouter, ReportLab, ffmpeg (subprocess), pytest. SQLite locally, Postgres in prod (same code).

## Hard rules
- Only edit files the prompt references with @ (or files it explicitly says to create). Ask before touching others.
- Services (`app/services/*`) are pure: `def fn(session, user_id, ...) -> dict | list`. No WhatsApp, no LLM, no HTTP inside services.
- The LLM never computes numbers. Every total/comparison comes from services.
- Money: `Decimal` everywhere, DB type `Numeric(12,2)`. Never float for storage. Format with `app/utils/money.format_rs`.
- Dates: business dates are `date` in Asia/Karachi via `app/utils/dates`. Timestamps stored UTC.
- Soft delete: transactions with `deleted_at` set are excluded from every query.
- Service functions return JSON-serialisable dicts (Decimal→str, date→ISO) only in `app/llm/dispatcher.py`; services may return Decimal/date.
- Never log tokens, secrets or full webhook bodies. Use `logging.getLogger(__name__)`.
- Type hints on every function. Small functions. Docstring one line.
- After changing services or llm code, run `pytest -q` and fix failures.
- Keep replies to the user short; WhatsApp formatting only (*bold*, _italic_), no markdown tables/headers.

## File map
- app/main.py — FastAPI app, lifespan(init_db, seed), /health, /privacy, includes webhook router
- app/config.py — Settings + cached `settings`
- app/db.py — engine, SessionLocal, session_scope(), init_db()
- app/models.py — User, Account, Category, Transaction, Budget, DebtEntry, SavingsGoal, GoalContribution, ConversationTurn, ProcessedMessage
- app/bootstrap.py — seed() idempotent; get_or_create_user()
- app/handler.py — handle_message(IncomingMessage)
- app/utils/dates.py, app/utils/money.py — helpers
- app/services/{categories,accounts,transactions,analytics,budgets,debts,goals,history}.py
- app/whatsapp/{client,payload,security,webhook}.py
- app/llm/{client,prompts,tools,dispatcher,agent}.py
- app/voice/transcribe.py
- app/reports/{pdf_builder,calendar_view}.py
- scripts/chat_cli.py, scripts/send_test_message.py
- tests/ — pytest, in-memory SQLite fixtures in conftest.py

## Data model (summary)
users(phone, name, default_account_id) · accounts(user_id, name, opening_balance, is_default) · categories(user_id, name, kind expense|income, is_default) · transactions(user_id, type expense|income, amount, category_id, account_id, description, occurred_on, source text|voice, raw_text, updated_at, deleted_at) · budgets(user_id, category_id, amount) · debt_entries(user_id, person, kind lent|borrowed|repaid_to_me|repaid_by_me, amount, account_id?, note, occurred_on) · savings_goals(user_id, name, target_amount, deadline?, status) · goal_contributions(goal_id, amount, occurred_on) · conversation_turns(user_id, role, content) · processed_messages(wa_message_id unique, user_phone)

Balance per account = opening + income − expense − lent + borrowed + repaid_to_me − repaid_by_me.

## Env vars
APP_ENV, LOG_LEVEL, DATABASE_URL, TIMEZONE, ALLOWED_NUMBERS, OWNER_NAME, WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_BUSINESS_ACCOUNT_ID, WA_VERIFY_TOKEN, WA_APP_SECRET, GRAPH_API_VERSION, OPENROUTER_API_KEY, LLM_MODEL, LLM_FALLBACK_MODEL, TRANSCRIBE_MODEL, HISTORY_TURNS

## Commands
- Run: `uvicorn app.main:app --reload --port 8000`
- Tests: `pytest -q`
- Local chat: `python -m scripts.chat_cli`
````

---
