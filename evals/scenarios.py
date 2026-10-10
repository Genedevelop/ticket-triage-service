from datetime import datetime

from backend.domain.ports import Providers
from backend.infrastructure.providers import sample_data


def load(providers: Providers, thread_name: str, messages_arrived: int, now: datetime, tool_down: str | None = None) -> None:
    # Step 1: Load the billing state the named thread's customer had once that many messages had arrived
    sample_data.load_billing(providers, thread_name, messages_arrived, now)
    # Step 2: Leave the status page unpublished when the scenario takes its tool down, so the provider errors
    if tool_down == "status_check":
        return
    if tool_down is not None:
        raise ValueError(f"no scenario takes {tool_down} down")
    # Step 3: Publish a status page that is green everywhere, also while a customer reports an outage
    sample_data.publish_status_page(providers, now)
