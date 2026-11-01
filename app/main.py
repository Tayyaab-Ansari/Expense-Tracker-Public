"""FastAPI application entrypoint: lifespan startup, health and privacy routes, webhook router."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.bootstrap import seed
from app.config import settings
from app.db import init_db, session_scope
from app.whatsapp.webhook import router as webhook_router

logger = logging.getLogger(__name__)

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database schema and seed the allowed users on startup."""
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
    """Health probe for the hosting platform."""
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    """Privacy policy page required for the WhatsApp app review."""
    return PRIVACY_HTML
