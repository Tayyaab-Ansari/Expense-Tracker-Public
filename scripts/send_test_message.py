"""Send a test WhatsApp message through the Cloud API."""

import asyncio
import logging
import sys

from app.config import settings
from app.whatsapp.client import WhatsAppError, send_text


async def main(to: str, body: str) -> int:
    """Send one text message and report the resulting message ids."""
    try:
        message_ids = await send_text(to, body)
    except WhatsAppError as exc:
        print(f"Failed: {exc}")
        return 1
    print(f"Sent to {to}: {', '.join(message_ids) or 'no message id returned'}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=settings.LOG_LEVEL)
    if len(sys.argv) != 3:
        print('Usage: python -m scripts.send_test_message <phone> "<message>"')
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
