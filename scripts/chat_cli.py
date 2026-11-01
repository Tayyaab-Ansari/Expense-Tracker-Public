"""Local command line chat loop for testing the bot without WhatsApp."""

import asyncio
import logging

from app.bootstrap import seed
from app.config import settings
from app.db import init_db, session_scope
from app.llm.agent import run
from app.models import User

QUIT_WORDS = {"exit", "quit"}


async def ask(user_id: int, text: str) -> None:
    """Run one turn through the agent and print what it produced."""
    with session_scope() as session:
        result = await run(session, session.get(User, user_id), text)
    print(result.reply)
    for document in result.documents:
        print(f"[document] {document.get('path')} ({document.get('filename')})")


async def main() -> int:
    """Chat with the bot in one event loop until the user quits."""
    logging.basicConfig(level=settings.LOG_LEVEL)
    init_db()
    with session_scope() as session:
        users = seed(session)
    if not users:
        print("No ALLOWED_NUMBERS configured; add one to .env first.")
        return 1

    owner = users[0]
    print(f"Chatting as {owner.name} ({owner.phone}). Type 'exit' to quit.")

    while True:
        try:
            text = (await asyncio.to_thread(input, "> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in QUIT_WORDS:
            return 0
        await ask(owner.id, text)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
