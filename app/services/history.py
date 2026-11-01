"""Conversation history service: store and fetch recent turns."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ConversationTurn

MAX_ROWS_PER_USER = 50


def save_turn(session: Session, user_id: int, role: str, content: str) -> ConversationTurn:
    """Store one conversation turn and prune the user's oldest rows."""
    turn = ConversationTurn(user_id=user_id, role=role, content=content)
    session.add(turn)
    session.flush()
    _prune(session, user_id)
    return turn


def _prune(session: Session, user_id: int) -> None:
    """Keep only the most recent rows for a user."""
    keep = session.scalars(
        select(ConversationTurn.id)
        .where(ConversationTurn.user_id == user_id)
        .order_by(ConversationTurn.id.desc())
        .limit(MAX_ROWS_PER_USER)
    ).all()
    if len(keep) < MAX_ROWS_PER_USER:
        return
    session.execute(
        delete(ConversationTurn).where(
            ConversationTurn.user_id == user_id, ConversationTurn.id.notin_(keep)
        )
    )
    session.flush()


def get_recent(session: Session, user_id: int, n_pairs: int) -> list[dict]:
    """Last n_pairs exchanges as chat messages, oldest first."""
    if n_pairs <= 0:
        return []
    rows = session.scalars(
        select(ConversationTurn)
        .where(ConversationTurn.user_id == user_id)
        .order_by(ConversationTurn.id.desc())
        .limit(n_pairs * 2)
    ).all()
    return [{"role": turn.role, "content": turn.content} for turn in reversed(rows)]
