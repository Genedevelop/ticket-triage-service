from typing import Any

from backend.application.tools.tool import NoArguments, Tool, ToolContext, ToolError
from backend.domain.ports import ProviderError


def _billing_lookup(_: NoArguments, context: ToolContext) -> dict[str, Any]:
    # Step 1: Refuse a ticket that names no customer, because there is no account to read
    if context.customer_id is None:
        raise ToolError("customer_unknown", "this ticket carries no customer id, so no billing account can be read")
    # Step 2: Read the account, handing a provider failure to the model as a tool error
    try:
        return context.providers.billing.lookup(context.customer_id, context.now)
    except ProviderError as error:
        raise ToolError.from_provider(error) from error


BILLING_LOOKUP = Tool(
    name="billing_lookup",
    description=(
        "Read this ticket's customer from the billing system: current plan, the last upgrade attempt and its status, "
        "and every payment attempt with its charge id, amount, state (authorized = a hold, captured, failed, refunded) "
        "and time. The customer is taken from the ticket; there are no arguments."
    ),
    arguments=NoArguments,
    handler=_billing_lookup,
)
