"""FastAPI application entrypoint: lifespan startup, health and privacy routes, webhook router."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.bootstrap import seed
from app.config import settings
from app.db import init_db, session_scope
from app.whatsapp.webhook import router as webhook_router

logger = logging.getLogger(__name__)

PRIVACY_TEXT = (
    "This is a personal expense tracker used by its owner only. "
    "It stores the messages you send it and the expenses they describe, "
    "and shares them with nobody. Message the owner to have your data deleted."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database schema and seed the allowed users on startup."""
    logging.basicConfig(level=settings.LOG_LEVEL)
    init_db()
    with session_scope() as session:
        users = seed(session)
    logger.info("Started in %s with %d seeded user(s)", settings.APP_ENV, len(users))
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/")
def healthcheck() -> dict[str, str]:
    """Report that the service is running."""
    return {"status": "up"}


@app.get("/privacy", response_class=PlainTextResponse)
def privacy() -> str:
    """Return the privacy notice required for the WhatsApp app review."""
    return PRIVACY_TEXT
