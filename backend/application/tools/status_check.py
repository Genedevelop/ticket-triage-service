from typing import Any

from backend.application.tools.tool import NoArguments, Tool, ToolContext, ToolError
from backend.domain.ports import ProviderError


def _status_check(_: NoArguments, context: ToolContext) -> dict[str, Any]:
    try:
        return context.providers.status.check(context.now)
    except ProviderError as error:
        raise ToolError.from_provider(error) from error


STATUS_CHECK = Tool(
    name="status_check",
    description=(
        "Read the public status page: the status of each component per region and when the page was last updated, "
        "and separately the incidents that are currently open. The page shows only what has been publicly "
        "acknowledged. There are no arguments."
    ),
    arguments=NoArguments,
    handler=_status_check,
)
