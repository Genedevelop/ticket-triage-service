from typing import Any

CUSTOMER_ID_FIELD = "id"


def split_customer(customer: dict[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
    # Step 1: Store nothing about the customer of a ticket that names none
    if customer is None:
        return None, None
    # Step 2: Separate the id, which the conversation is found by, from the rest of the record
    return customer.get(CUSTOMER_ID_FIELD), {name: value for name, value in customer.items() if name != CUSTOMER_ID_FIELD}


def customer_record(conversation: dict[str, Any]) -> dict[str, Any]:
    return {CUSTOMER_ID_FIELD: conversation["customer_id"], **(conversation["customer_metadata"] or {})}
