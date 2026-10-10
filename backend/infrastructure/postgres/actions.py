from datetime import timedelta
from typing import Any, Sequence
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.core import logging as log
from backend.core.errors import Internal
from backend.domain import action_status

# the predicate of the actions_live_key index: the rows that hold their key
HOLDS_KEY = """(status IN ('pending_approval', 'approved', 'executing', 'needs_review')
                OR (hold_key AND status IN ('succeeded', 'rejected')))"""

INSERT = """
    INSERT INTO actions (kind, key, hold_key, payload, evidence, status)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
    RETURNING *
"""

CLAIM = """
    UPDATE actions SET status = 'executing', claimed_at = now(), attempts = attempts + 1
     WHERE id = %(id)s AND attempts < %(max)s
       AND (status = 'approved'
            OR (status = 'executing' AND (claimed_at IS NULL OR claimed_at < now() - %(lease)s)))
    RETURNING *
"""

PARK = """
    UPDATE actions SET status = %(parked)s
     WHERE id = %(id)s AND status = 'executing' AND attempts >= %(max)s
       AND (claimed_at IS NULL OR claimed_at < now() - %(lease)s)
    RETURNING id
"""

# how often a proposal looks again when the holder of its key vanished between the insert and the read
PROPOSE_ROUNDS = 3


def _write_event(
    conn: Any,
    action_id: UUID,
    event_type: str,
    *,
    actor: str,
    conversation_id: UUID | None = None,
    detail: Any = None,
    outcome: str | None = None,
) -> None:
    # Step 1: Write the event on the caller's connection, so it commits with the status change
    conn.execute(
        "INSERT INTO action_events (action_id, type, actor, conversation_id, detail) VALUES (%s, %s, %s, %s, %s)",
        (action_id, event_type, actor, conversation_id, Jsonb(detail) if detail is not None else None),
    )
    # Step 2: Log the transition when the event moved the action to a status
    if outcome is not None:
        log.event("action.transition", outcome=outcome, action_id=action_id, transition=event_type, actor=actor)


def propose(
    pool: ConnectionPool,
    *,
    kind: str,
    key: str,
    is_key_held: bool,
    status: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    actor: str,
    conversation_id: UUID,
    detail: dict[str, Any],
    approver: str | None = None,
) -> tuple[dict[str, Any], bool]:
    for _ in range(PROPOSE_ROUNDS):
        with pool.connection() as conn, conn.transaction():
            # Step 1: Insert the action, which the unique index refuses while another action holds the key
            cursor = conn.cursor(row_factory=dict_row)
            created = cursor.execute(INSERT, (kind, key, is_key_held, Jsonb(payload), Jsonb(evidence), status)).fetchone()
            if created is not None:
                _write_event(conn, created["id"], "proposed", actor=actor, conversation_id=conversation_id, detail=detail, outcome=status)
                if approver is not None:
                    _write_event(conn, created["id"], action_status.APPROVED, actor=approver, outcome=action_status.APPROVED)
                return created, True
            # Step 2: Join the action that holds the key, looking again when it vanished between the insert and the read
            holder = cursor.execute(f"SELECT * FROM actions WHERE key = %s AND {HOLDS_KEY}", (key,)).fetchone()
            if holder is not None:
                _write_event(conn, holder["id"], "joined", actor=actor, conversation_id=conversation_id, detail=detail)
                return holder, False
    # Step 3: Give up after the last round, which no ordinary interleaving reaches
    raise Internal(f"the key {key} was neither free nor held after {PROPOSE_ROUNDS} rounds")


def decide(
    pool: ConnectionPool, action_id: UUID, status: str, *, actor: str, detail: dict[str, Any] | None = None
) -> bool:
    # Step 1: Refuse a status the state machine does not allow after pending
    action_status.check(action_status.PENDING, status)
    # Step 2: Move the action only if it is still pending, and write the event in the same transaction
    with pool.connection() as conn, conn.transaction():
        moved = conn.execute(
            "UPDATE actions SET status = %s WHERE id = %s AND status = 'pending_approval' RETURNING id", (status, action_id)
        ).fetchone()
        if moved is None:
            return False
        _write_event(conn, action_id, status, actor=actor, detail=detail, outcome=status)
    return True


def claim(pool: ConnectionPool, action_id: UUID, *, max_attempts: int, lease_s: float) -> dict[str, Any] | None:
    # Step 1: Take the action if it is approved or its last claim ran out, counting the attempt
    with pool.connection() as conn, conn.transaction():
        claimed = conn.cursor(row_factory=dict_row).execute(
            CLAIM, {"id": action_id, "max": max_attempts, "lease": timedelta(seconds=lease_s)}
        ).fetchone()
        # Step 2: Write the claim's event in the same transaction
        if claimed is not None:
            _write_event(conn, action_id, "claimed", actor="executor", detail={"attempt": claimed["attempts"]}, outcome=action_status.EXECUTING)
    return claimed


def record_outcome(
    pool: ConnectionPool, action_id: UUID, attempt: int, status: str, *, provider_ref: str | None, detail: dict[str, Any]
) -> bool:
    # Step 1: Refuse a status the state machine does not allow after executing
    action_status.check(action_status.EXECUTING, status)
    # Step 2: Write the outcome only if this attempt is still the current one, with its event in one transaction
    with pool.connection() as conn, conn.transaction():
        written = conn.execute(
            """
            UPDATE actions SET status = %s, provider_ref = %s
             WHERE id = %s AND status = 'executing' AND attempts = %s
            RETURNING id
            """,
            (status, provider_ref, action_id, attempt),
        ).fetchone()
        if written is None:
            return False
        detail = {"attempt": attempt, "outcome": status, **detail}
        _write_event(conn, action_id, "provider_result", actor="executor", detail=detail, outcome=status)
    return True


def release(pool: ConnectionPool, action_id: UUID, attempt: int, *, detail: dict[str, Any]) -> bool:
    # Step 1: Clear the lease only if this attempt is still the current one
    with pool.connection() as conn, conn.transaction():
        released = conn.execute(
            "UPDATE actions SET claimed_at = NULL WHERE id = %s AND status = 'executing' AND attempts = %s RETURNING id",
            (action_id, attempt),
        ).fetchone()
        if released is None:
            return False
        # Step 2: Write the unknown outcome as an event in the same transaction
        detail = {"attempt": attempt, "outcome": "unknown", **detail}
        _write_event(conn, action_id, "provider_result", actor="executor", detail=detail, outcome="unknown")
    return True


def park(pool: ConnectionPool, action_id: UUID, parked: str, *, max_attempts: int, lease_s: float) -> bool:
    # Step 1: Refuse a status the state machine does not allow after executing
    action_status.check(action_status.EXECUTING, parked)
    # Step 2: Park the action only if its attempts are spent and its lease is free, with its event alongside
    with pool.connection() as conn, conn.transaction():
        moved = conn.execute(
            PARK, {"id": action_id, "parked": parked, "max": max_attempts, "lease": timedelta(seconds=lease_s)}
        ).fetchone()
        if moved is None:
            return False
        _write_event(conn, action_id, "parked", actor="executor", detail={"status": parked}, outcome=parked)
    return True


def resolve(pool: ConnectionPool, action_id: UUID, status: str, *, actor: str, detail: dict[str, Any]) -> bool:
    # Step 1: Refuse a status the state machine does not allow after needs_review
    action_status.check(action_status.NEEDS_REVIEW, status)
    # Step 2: Move the action only if it is still in needs_review, with its event in the same transaction
    with pool.connection() as conn, conn.transaction():
        moved = conn.execute(
            "UPDATE actions SET status = %s WHERE id = %s AND status = 'needs_review' RETURNING id", (status, action_id)
        ).fetchone()
        if moved is None:
            return False
        _write_event(conn, action_id, "resolved", actor=actor, detail=detail, outcome=status)
    return True


def free_unfinished(pool: ConnectionPool, kind: str) -> list[UUID]:
    # Step 1: Clear the lease of every approved or executing action of the kind
    with pool.connection() as conn:
        freed = conn.execute(
            """
            UPDATE actions SET claimed_at = NULL
             WHERE kind = %s AND status IN ('approved', 'executing')
            RETURNING created_at, id
            """,
            (kind,),
        ).fetchall()
    # Step 2: Return their ids, oldest first
    return [action_id for _, action_id in sorted(freed)]


def _with_events(cursor: Any, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for action in actions:
        action["events"] = cursor.execute(
            """
            SELECT type, actor, conversation_id, detail, created_at AS at FROM action_events
             WHERE action_id = %s ORDER BY id
            """,
            (action["id"],),
        ).fetchall()
    return actions


def get(pool: ConnectionPool, action_id: UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        actions = cursor.execute("SELECT * FROM actions WHERE id = %s", (action_id,)).fetchall()
        return _with_events(cursor, actions)[0] if actions else None


def touched_by(cursor: Any, conversation_id: UUID) -> list[dict[str, Any]]:
    actions = cursor.execute(
        """
        SELECT * FROM actions
         WHERE id IN (SELECT action_id FROM action_events
                       WHERE conversation_id = %s AND type IN ('proposed', 'joined'))
         ORDER BY created_at, id
        """,
        (conversation_id,),
    ).fetchall()
    return _with_events(cursor, actions)


def for_conversation(pool: ConnectionPool, conversation_id: UUID) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return touched_by(conn.cursor(row_factory=dict_row), conversation_id)


def fail_unfinished(cursor: Any, conversation_id: UUID, turn_id: UUID, kinds: Sequence[str]) -> None:
    # Step 1: Confirm the state machine lets an approved or executing action fail
    action_status.check(action_status.APPROVED, action_status.FAILED)
    action_status.check(action_status.EXECUTING, action_status.FAILED)
    # Step 2: Fail what the turn proposed of these kinds and left unfinished, so no provider is called for it
    failed = cursor.execute(
        """
        UPDATE actions SET status = 'failed'
         WHERE kind = ANY(%s) AND status IN ('approved', 'executing')
           AND id IN (SELECT action_id FROM action_events
                       WHERE type = 'proposed' AND conversation_id = %s AND detail ->> 'turn_id' = %s)
        RETURNING id
        """,
        (list(kinds), conversation_id, str(turn_id)),
    ).fetchall()
    # Step 3: Write an event for each one, saying that its outcome is unknown
    for action in failed:
        detail = {"status": action_status.FAILED, "reason": "outcome_unknown", "turn_id": str(turn_id)}
        _write_event(cursor.connection, action["id"], "parked", actor="turn_cleanup", detail=detail, outcome=action_status.FAILED)


def proposed_in_turn(pool: ConnectionPool, kind: str, conversation_id: UUID, turn_id: UUID) -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT count(*) FROM action_events e JOIN actions a ON a.id = e.action_id
             WHERE a.kind = %s AND e.conversation_id = %s AND e.type IN ('proposed', 'joined')
               AND e.detail ->> 'turn_id' = %s
            """,
            (kind, conversation_id, str(turn_id)),
        ).fetchone()[0]


def created_since_last_decision(pool: ConnectionPool, conversation_id: UUID, kind: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        actions = cursor.execute(
            """
            SELECT * FROM actions
             WHERE kind = %(kind)s
               AND id IN (SELECT action_id FROM action_events WHERE conversation_id = %(conversation)s AND type = 'proposed')
               AND created_at > coalesce(
                       (SELECT max(created_at) FROM decisions WHERE conversation_id = %(conversation)s), '-infinity')
             ORDER BY created_at, id
            """,
            {"kind": kind, "conversation": conversation_id},
        ).fetchall()
        return _with_events(cursor, actions)
