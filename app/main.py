"""FastAPI application entrypoint: lifespan startup, health and privacy routes, webhook router."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import SecretStr
from sqlalchemy import text

from app.bootstrap import seed
from app.config import settings
from app.db import init_db, session_scope
from app.whatsapp.webhook import router as webhook_router

logger = logging.getLogger(__name__)

EXPECTED_SETTINGS = (
    "ALLOWED_NUMBERS",
    "OWNER_NAME",
    "WA_ACCESS_TOKEN",
    "WA_PHONE_NUMBER_ID",
    "WA_VERIFY_TOKEN",
    "WA_APP_SECRET",
    "OPENROUTER_API_KEY",
)
REQUIRED_IN_PRODUCTION = (
    "WA_APP_SECRET",
    "WA_ACCESS_TOKEN",
    "OPENROUTER_API_KEY",
    "ALLOWED_NUMBERS",
)

PRIVACY_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Privacy Policy</title></head>
<body>
<h1>Privacy Policy</h1>
<p>This is a personal expense tracking bot used by its owner only.</p>
<p>It stores the messages sent to it and the expenses, incomes, debts and savings
they describe. The data is kept privately and is never shared, sold or used for
advertising.</p>
<p>No other person can use this bot: messages from numbers that are not on the
owner's allow list are ignored.</p>
<p>To ask for your data to be deleted, contact
<a href="mailto:owner@example.com">owner@example.com</a>.</p>
</body>
</html>
"""


def _missing(names: tuple[str, ...]) -> list[str]:
    """Names of the settings that are empty."""
    missing = []
    for name in names:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not str(value).strip():
            missing.append(name)
    return missing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Check the configuration, create the schema and seed the allowed users."""
    missing = _missing(EXPECTED_SETTINGS)
    if missing:
        logger.warning("Missing configuration: %s", ", ".join(missing))

    blocking = _missing(REQUIRED_IN_PRODUCTION)
    if blocking and settings.is_production:
        raise RuntimeError(f"Cannot start in production without: {', '.join(blocking)}")

    init_db()
    with session_scope() as session:
        users = seed(session)
    logger.info("Started in %s with %d seeded user(s)", settings.APP_ENV, len(users))
    yield


logging.basicConfig(level=settings.LOG_LEVEL)
app = FastAPI(lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/")
def root() -> dict[str, str]:
    """Report that the service is running."""
    return {"status": "up"}


@app.get("/health")
def health() -> dict[str, str]:
    """Health probe that also checks the database connection."""
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check could not reach the database")
        raise HTTPException(status_code=503, detail={"status": "error", "db": "error"}) from None
    return {"status": "ok", "db": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    """Privacy policy page required for the WhatsApp app review."""
    return PRIVACY_HTML
