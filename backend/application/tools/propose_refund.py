from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.application.actions import refund
from backend.application.tools.tool import Tool, ToolContext, ToolError
from backend.domain.ports import ProviderError


class ProposeRefundArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_id: str
    reason: str


def _propose_refund(arguments: ProposeRefundArguments, context: ToolContext) -> dict[str, Any]:
    # Step 1: Refuse a ticket that names no customer, because a refund is proposed for a customer's own charge
    if context.customer_id is None:
        raise ToolError("customer_unknown", "this ticket carries no customer id, so no refund can be proposed")
    # Step 2: Propose the refund, or join the proposal that already holds the charge
    try:
        action, is_created = refund.propose(
            context.pool,
            context.providers.billing,
            conversation_id=context.conversation_id,
            turn_id=context.turn_id,
            customer_id=context.customer_id,
            charge_id=arguments.charge_id,
            reason=arguments.reason,
            now=context.now,
            timeout_s=context.timing.provider_timeout_s,
        )
    except refund.RefundDenied as denied:
        # Step 3: Tell the model why the refund was denied, with the rejected proposal when a person rejected it
        result = {"denied": True, "reason": denied.reason, "message": denied.message}
        if denied.action is not None:
            result["action"] = {"id": str(denied.action["id"]), "kind": denied.action["kind"], "status": denied.action["status"]}
        return result
    except ProviderError as error:
        raise ToolError.from_provider(error) from error
    # Step 4: Report the proposal, saying plainly that nothing was refunded
    return {
        "denied": False,
        "action": {"id": str(action["id"]), "kind": action["kind"], "status": action["status"], **action["payload"]},
        "already_proposed": not is_created,
        "note": "Nothing was refunded. A person decides on this proposal; code executes it only after their approval.",
    }


PROPOSE_REFUND = Tool(
    name="propose_refund",
    description=(
        "Propose a full refund of one captured charge of this ticket's customer, by its charge id from billing_lookup. "
        "This refunds nothing: the proposal waits for a person's approval. The amount is taken from the charge. "
        "Returns the proposal and its status, or a denial with the reason."
    ),
    arguments=ProposeRefundArguments,
    handler=_propose_refund,
    is_effect=True,
)
