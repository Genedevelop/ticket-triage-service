import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.core.errors import OtherCustomer
from backend.infrastructure.postgres import actions
from backend.infrastructure.postgres.turns import Fallback, open_turn, read_steps

# the latest decision that is not a fallback and covers the last message of the ticket's thread
COMPLETE_TRIAGE = """
    SELECT d.body FROM decisions d JOIN turns t ON t.id = d.turn_id
     WHERE d.conversation_id = %(conversation)s AND d.body ->> 'source' <> 'fallback'
       AND t.last_message_id >= (SELECT max(id) FROM messages
                                  WHERE conversation_id = %(conversation)s AND author IN ('customer', 'support_agent'))
     ORDER BY d.version DESC LIMIT 1
"""


@dataclass(frozen=True)
class Started:
    conversation_id: UUID
    turn_id: UUID | None
    is_created: bool = False
    # the stored decision that already answers the request, when no turn was opened
    replayed_decision: dict[str, Any] | None = None


def _message_key(message: dict[str, Any]) -> str:
    # Step 1: Use the sender's own id when the message carries one
    if message.get("id"):
        return f"id:{message['id']}"
    # Step 2: Otherwise hash the author, the time and the body, so the same message sent again is recognised
    text = "\n".join([message["author"], message["sent_at"].isoformat(), message["body"]])
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _find_or_create(
    cursor: Any, ticket_id: str | None, customer_id: str | None, customer_metadata: dict[str, Any] | None
) -> tuple[UUID, bool]:
    # Step 1: Insert the conversation; a second sender of the same new ticket waits here, then finds the first's
    inserted = cursor.execute(
        """
        INSERT INTO conversations (ticket_id, customer_id, customer_metadata) VALUES (%s, %s, %s)
        ON CONFLICT (ticket_id) DO NOTHING RETURNING id
        """,
        (ticket_id, customer_id, Jsonb(customer_metadata) if customer_metadata is not None else None),
    ).fetchone()
    if inserted is not None:
        return inserted["id"], True
    # Step 2: Read the conversation that already holds the ticket id
    existing = cursor.execute("SELECT id, customer_id FROM conversations WHERE ticket_id = %s", (ticket_id,)).fetchone()
    # Step 3: Refuse a ticket stored under another customer; an id missing on either side is no mismatch
    if customer_id is not None and existing["customer_id"] not in (None, customer_id):
        raise OtherCustomer(ticket_id)
    return existing["id"], False


def _store_unseen_messages(cursor: Any, conversation_id: UUID, messages: list[dict[str, Any]]) -> None:
    for message in messages:
        cursor.execute(
            """
            INSERT INTO messages (conversation_id, message_key, sender_id, author, body, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, message_key) DO NOTHING
            """,
            (conversation_id, _message_key(message), message.get("id"), message["author"], message["body"], message["sent_at"]),
        )


def start_ingest(
    pool: ConnectionPool,
    *,
    ticket_id: str | None,
    customer_id: str | None,
    customer_metadata: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    lease_s: float,
    fallback: Fallback,
) -> Started:
    with pool.connection() as conn, conn.transaction():
        cursor = conn.cursor(row_factory=dict_row)
        # Step 1: Find or create the ticket's conversation and store the messages not seen before
        conversation_id, is_created = _find_or_create(cursor, ticket_id, customer_id, customer_metadata)
        _store_unseen_messages(cursor, conversation_id, messages)
        # Step 2: Answer with the stored decision when a triage already covers the thread's last message
        complete = cursor.execute(COMPLETE_TRIAGE, {"conversation": conversation_id}).fetchone()
        if complete is not None:
            return Started(conversation_id, None, replayed_decision=complete["body"])
        # Step 3: Open a turn on the last message, in the same transaction as everything stored above
        last_message_id = cursor.execute(
            "SELECT max(id) AS id FROM messages WHERE conversation_id = %s", (conversation_id,)
        ).fetchone()["id"]
        turn_id = open_turn(cursor, conversation_id, "ingest", last_message_id, lease_s, fallback)
    return Started(conversation_id, turn_id, is_created=is_created)


def start_operator_turn(
    pool: ConnectionPool,
    conversation_id: UUID,
    body: str,
    now: datetime,
    *,
    idempotency_key: str | None,
    lease_s: float,
    fallback: Fallback,
) -> Started | None:
    with pool.connection() as conn, conn.transaction():
        cursor = conn.cursor(row_factory=dict_row)
        # Step 1: Report a conversation that does not exist as None
        if cursor.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,)).fetchone() is None:
            return None
        # Step 2: Look for the message under the sender's key; without a key the same words are a new message
        message_key = f"key:{idempotency_key}" if idempotency_key else f"operator:{uuid4()}"
        stored = cursor.execute(
            "SELECT id FROM messages WHERE conversation_id = %s AND message_key = %s", (conversation_id, message_key)
        ).fetchone()
        # Step 3: Answer with the stored decision when an operator turn already covered that message
        if stored is not None:
            covered = cursor.execute(
                """
                SELECT d.body FROM decisions d JOIN turns t ON t.id = d.turn_id
                 WHERE t.conversation_id = %s AND t.kind = 'operator' AND t.last_message_id = %s
                 ORDER BY d.version DESC LIMIT 1
                """,
                (conversation_id, stored["id"]),
            ).fetchone()
            if covered is not None:
                return Started(conversation_id, None, replayed_decision=covered["body"])
        # Step 4: Open the turn before storing the message, so a busy conversation stores nothing
        turn_id = open_turn(cursor, conversation_id, "operator", stored["id"] if stored else None, lease_s, fallback)
        # Step 5: Store a message not seen before and point the turn at it
        if stored is None:
            stored = cursor.execute(
                """
                INSERT INTO messages (conversation_id, message_key, author, body, sent_at)
                VALUES (%s, %s, 'operator', %s, %s) RETURNING id
                """,
                (conversation_id, message_key, body, now),
            ).fetchone()
            cursor.execute("UPDATE turns SET last_message_id = %s WHERE id = %s", (stored["id"], turn_id))
    return Started(conversation_id, turn_id)


def load(pool: ConnectionPool, conversation_id: UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        # Step 1: Read the conversation, reporting one that does not exist as None
        conversation = cursor.execute(
            "SELECT id, ticket_id, customer_id, customer_metadata FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()
        if conversation is None:
            return None
        # Step 2: Read its messages, every decision version, and its turns with their steps
        messages = cursor.execute(
            """
            SELECT sender_id AS id, author, body, sent_at FROM messages
             WHERE conversation_id = %s ORDER BY sent_at, messages.id
            """,
            (conversation_id,),
        ).fetchall()
        decisions = cursor.execute(
            "SELECT body FROM decisions WHERE conversation_id = %s ORDER BY version", (conversation_id,)
        ).fetchall()
        turns = cursor.execute(
            "SELECT id, kind, status FROM turns WHERE conversation_id = %s ORDER BY created_at, id", (conversation_id,)
        ).fetchall()
        for turn in turns:
            turn["steps"] = read_steps(cursor, turn["id"])
    # Step 3: Number the messages in the order they were sent
    for number, message in enumerate(messages, start=1):
        message["number"] = number
    # Step 4: Add every action the conversation proposed or joined
    return {
        **conversation,
        "messages": messages,
        "decisions": [row["body"] for row in decisions],
        "turns": turns,
        "actions": actions.for_conversation(pool, conversation_id),
    }
