from typing import Any
from uuid import UUID

from backend.application.customer import customer_record
from backend.infrastructure.postgres import conversations as store


def read(pool: Any, conversation_id: UUID) -> dict[str, Any] | None:
    # Step 1: Read everything stored about the conversation, reporting one that does not exist as None
    conversation = store.load(pool, conversation_id)
    if conversation is None:
        return None
    # Step 2: Put the customer's id back into the customer record in place of the two stored columns
    customer = customer_record(conversation)
    del conversation["customer_id"], conversation["customer_metadata"]
    return {**conversation, "customer": customer}
