from typing import Any
from uuid import UUID


# the members of a problem document, which the details written beside them may not replace
RESERVED_DETAIL_KEYS = frozenset({"type", "title", "status", "detail"})


class AppError(Exception):
    # the stable machine name, in kebab-case
    code = "internal-error"
    title = "Internal error"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        # Step 1: Refuse details that would replace a member of the problem document they are written into
        reserved = RESERVED_DETAIL_KEYS & set(details or {})
        if reserved:
            raise ReservedDetail(f"{type(self).__name__} names its details {', '.join(sorted(reserved))}")
        # Step 2: Keep the message and the details, taking the title when no message is given
        self.message = message or self.title
        self.details = details or {}
        super().__init__(self.message)


class NotFound(AppError):
    code = "not-found"
    title = "Not found"

    def __init__(self, what: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{what} not found", details=details)


class Conflict(AppError):
    code = "conflict"
    title = "Conflict"


class Invalid(AppError):
    code = "invalid"
    title = "Invalid"


class Denied(AppError):
    code = "denied"
    title = "Denied"


class Unavailable(AppError):
    code = "unavailable"
    title = "Unavailable"


class Misconfigured(AppError):
    code = "misconfigured"
    title = "Misconfigured"


class CallFailed(AppError):
    code = "call-failed"
    title = "Call failed"


class Internal(AppError):
    code = "internal-error"
    title = "Internal error"


class ReservedDetail(Internal):
    code = "reserved-detail"
    title = "Reserved detail"


class Busy(Conflict):
    code = "conversation-busy"
    title = "Conversation busy"

    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(
            "another turn is running on this conversation; send the request again when it has finished",
            details={"conversation_id": str(conversation_id)},
        )


class Lost(Conflict):
    code = "turn-lost"
    title = "Turn lost its conversation"

    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(
            "this turn ran past its lease and another turn took the conversation; nothing was decided by this request",
            details={"conversation_id": str(conversation_id)},
        )


class OtherCustomer(Conflict):
    code = "ticket-customer-mismatch"
    title = "Ticket belongs to another customer"

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            "this ticket_id is stored under a different customer id; nothing was stored and nothing was decided by this request",
            details={"ticket_id": ticket_id},
        )
