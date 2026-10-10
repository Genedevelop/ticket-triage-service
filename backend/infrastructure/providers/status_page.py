from datetime import datetime
from typing import Any

from backend.domain.ports import ProviderError
from backend.infrastructure.postgres import incident_fixtures, status_page_fixtures


class MockStatusPage:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def add_page(self, components: list[dict[str, str]], page_updated_at: datetime) -> None:
        status_page_fixtures.add_status_page(self._pool, {"page_updated_at": page_updated_at.isoformat(), "components": components})

    def check(self, now: datetime) -> dict[str, Any]:
        # Step 1: Read the published page, which a status page that is down does not have
        page = status_page_fixtures.get_status_page(self._pool)
        if page is None:
            raise ProviderError("status_page_unavailable", "the status page could not be read", is_retryable=True)
        # Step 2: Report the page beside the incidents the incident system has open now
        return {
            "source": "public status page",
            "observed_at": now.isoformat(),
            "page_updated_at": page["page_updated_at"],
            "components": page["components"],
            "open_incidents": [
                {**incident, "opened_at": incident["opened_at"].isoformat()} for incident in incident_fixtures.list_open(self._pool)
            ],
        }
