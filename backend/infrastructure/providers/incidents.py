from typing import Any
from uuid import uuid4

from backend.infrastructure.postgres import incident_fixtures
from backend.infrastructure.providers.faults import ISSUED_ID_HEX_CHARS, fault_after_acting, fault_before_acting


class MockIncidents:
    def __init__(self, pool: Any) -> None:
        # Step 1: Keep the database the incidents live in
        self._pool = pool
        # Step 2: Start with no fault queued; a test queues one per coming open call
        self.faults: list[str] = []

    def open(self, dedup_key: str, *, component: str, region: str, summary: str, timeout_s: float) -> dict[str, Any]:
        # Step 1: Apply a queued fault that stops the call before the provider acts
        fault = fault_before_acting(self.faults, timeout_s)
        # Step 2: Open the incident, or attach to the one open for the dedup key
        incident, already_open = incident_fixtures.open_or_attach(
            self._pool,
            incident_id=f"inc_{uuid4().hex[:ISSUED_ID_HEX_CHARS]}",
            dedup_key=dedup_key,
            component=component,
            region=region,
            summary=summary,
        )
        # Step 3: Apply a queued fault that loses the answer of the call the provider acted on
        fault_after_acting(fault)
        return {"incident_id": incident["incident_id"], "already_open": already_open}

    def list_open(self) -> list[dict[str, Any]]:
        return incident_fixtures.list_open(self._pool)

    def resolve(self, incident_id: str) -> None:
        incident_fixtures.resolve(self._pool, incident_id)
