from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.core.errors import Busy, Lost
from backend.domain.policy import kinds_approved_by_their_policy
from backend.infrastructure.postgres import actions

# builds the fallback decision of a dead ingest turn from its steps, the conversation's actions and the previous decision
Fallback = Callable[[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None], dict[str, Any]]

# the lease runs on the database clock
CLAIM = """
    UPDATE conversations SET busy_turn = %(turn)s, busy_until = now() + %(lease)s
     WHERE id = %(conversation)s AND (busy_turn IS NULL OR busy_until < now())
    RETURNING id
"""

WRITE_DECISION = """
    INSERT INTO decisions (conversation_id, turn_id, version, body)
    SELECT %(conversation)s, %(turn)s, next.version, %(body)s::jsonb || jsonb_build_object('version', next.version)
      FROM (SELECT coalesce(max(version), 0) + 1 AS version FROM decisions WHERE conversation_id = %(conversation)s) next
"""

# only the turn holding the conversation matches, so a turn that lost it writes nothing
RELEASE_AND_WRITE = """
    WITH released AS (
        UPDATE conversations SET busy_turn = NULL, busy_until = NULL
         WHERE id = %(conversation)s AND busy_turn = %(turn)s
        RETURNING id
    ), written AS (
        INSERT INTO decisions (conversation_id, turn_id, version, body)
        SELECT released.id, %(turn)s, next.version, %(body)s::jsonb || jsonb_build_object('version', next.version)
          FROM released,
               (SELECT coalesce(max(version), 0) + 1 AS version FROM decisions WHERE conversation_id = %(conversation)s) next
        RETURNING body
    ), closed AS (
        UPDATE turns SET status = 'completed', finished_at = now()
         WHERE id = %(turn)s AND EXISTS (SELECT 1 FROM released)
    ), answered AS (
        INSERT INTO messages (conversation_id, message_key, author, body, sent_at)
        SELECT released.id, %(reply_key)s, 'assistant', %(reply)s::text, %(sent_at)s::timestamptz
          FROM released WHERE %(reply)s::text IS NOT NULL
    )
    SELECT body FROM written
"""


def open_turn(
    cursor: Any, conversation_id: UUID, kind: str, last_message_id: int | None, lease_s: float, fallback: Fallback
) -> UUID:
    # Step 1: Claim the conversation for the new turn, refusing while another turn holds it
    turn_id = uuid4()
    claimed = cursor.execute(
        CLAIM, {"turn": turn_id, "lease": timedelta(seconds=lease_s), "conversation": conversation_id}
    ).fetchone()
    if claimed is None:
        raise Busy(conversation_id)
    # Step 2: Clean up after a turn that died while it held the conversation
    _abandon_running_turns(cursor, conversation_id, fallback)
    # Step 3: Record the new turn as running
    cursor.execute(
        "INSERT INTO turns (id, conversation_id, kind, status, last_message_id) VALUES (%s, %s, %s, 'running', %s)",
        (turn_id, conversation_id, kind, last_message_id),
    )
    return turn_id


def _abandon_running_turns(cursor: Any, conversation_id: UUID | None, fallback: Fallback) -> None:
    # Step 1: Mark every running turn abandoned, in one conversation or in all of them when none is given
    dead = cursor.execute(
        """
        UPDATE turns SET status = 'abandoned', finished_at = now()
         WHERE status = 'running' AND (%(conversation)s::uuid IS NULL OR conversation_id = %(conversation)s)
        RETURNING id, conversation_id, kind, created_at
        """,
        {"conversation": conversation_id},
    ).fetchall()
    for turn in sorted(dead, key=lambda turn: (turn["created_at"], turn["id"])):
        # Step 2: Fail what the dead turn left unfinished of the kinds its policy approves, which run inside a turn
        actions.fail_unfinished(cursor, turn["conversation_id"], turn["id"], kinds_approved_by_their_policy())
        # Step 3: Write a fallback decision for a dead ingest turn, so its ticket still reaches a person
        if turn["kind"] == "ingest":
            previous = cursor.execute(
                "SELECT body FROM decisions WHERE conversation_id = %s ORDER BY version DESC LIMIT 1",
                (turn["conversation_id"],),
            ).fetchone()
            body = fallback(
                read_steps(cursor, turn["id"]),
                actions.touched_by(cursor, turn["conversation_id"]),
                previous["body"] if previous else None,
            )
            cursor.execute(WRITE_DECISION, {"conversation": turn["conversation_id"], "turn": turn["id"], "body": Jsonb(body)})


def clean_up_dead_turns(pool: ConnectionPool, fallback: Fallback) -> None:
    with pool.connection() as conn, conn.transaction():
        # Step 1: Clean up every running turn, which is safe only at start-up when nothing else runs
        _abandon_running_turns(conn.cursor(row_factory=dict_row), None, fallback)
        # Step 2: Free every conversation a dead turn still held
        conn.execute("UPDATE conversations SET busy_turn = NULL, busy_until = NULL WHERE busy_turn IS NOT NULL")


def is_held_by(pool: ConnectionPool, conversation_id: UUID, turn_id: UUID) -> bool:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND busy_turn = %s AND busy_until > now()", (conversation_id, turn_id)
        ).fetchone() is not None


def record_step(
    pool: ConnectionPool,
    turn_id: UUID,
    *,
    kind: str,
    name: str,
    arguments: Any,
    result: Any,
    is_ok: bool,
    duration_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    # Step 1: Build the step as the turn keeps it
    step = {
        "kind": kind,
        "name": name,
        "arguments": arguments,
        "result": result,
        "ok": is_ok,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    # Step 2: Append it to the turn's audit trail
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO steps (turn_id, kind, name, arguments, result, ok, duration_ms, input_tokens, output_tokens)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (turn_id, kind, name, Jsonb(arguments), Jsonb(result), is_ok, duration_ms, input_tokens, output_tokens),
        )
    return step


def complete_turn(
    pool: ConnectionPool,
    conversation_id: UUID,
    turn_id: UUID,
    decision: dict[str, Any],
    reply_sent_at: datetime | None = None,
) -> dict[str, Any]:
    # Step 1: Take the reply to the operator, which only an operator turn sends
    reply = decision["operator_reply"] if reply_sent_at is not None else None
    # Step 2: Release the conversation and write the decision in one statement
    with pool.connection() as conn:
        written = conn.execute(
            RELEASE_AND_WRITE,
            {
                "conversation": conversation_id,
                "turn": turn_id,
                "body": Jsonb(decision),
                "reply_key": f"turn:{turn_id}",
                "reply": reply or None,
                "sent_at": reply_sent_at,
            },
        ).fetchone()
    # Step 3: Report a turn that no longer held the conversation, for which nothing was written
    if written is None:
        raise Lost(conversation_id)
    return written[0]


def fail_turn(pool: ConnectionPool, conversation_id: UUID, turn_id: UUID) -> None:
    with pool.connection() as conn, conn.transaction():
        # Step 1: Release the conversation if the turn still holds it
        conn.execute(
            "UPDATE conversations SET busy_turn = NULL, busy_until = NULL WHERE id = %s AND busy_turn = %s",
            (conversation_id, turn_id),
        )
        # Step 2: Close the turn as failed
        conn.execute("UPDATE turns SET status = 'failed', finished_at = now() WHERE id = %s AND status = 'running'", (turn_id,))


def read_steps(cursor: Any, turn_id: UUID) -> list[dict[str, Any]]:
    return cursor.execute(
        """
        SELECT kind, name, arguments, result, ok, duration_ms, input_tokens, output_tokens
          FROM steps WHERE turn_id = %s ORDER BY id
        """,
        (turn_id,),
    ).fetchall()
