# the billing, status page and incidents mocks and the sample data; imports itself, backend.infrastructure.postgres, backend.domain and backend.core
from typing import Any

from backend.domain.ports import Providers
from backend.infrastructure.providers.billing import MockBilling
from backend.infrastructure.providers.incidents import MockIncidents
from backend.infrastructure.providers.status_page import MockStatusPage


def mock_providers(pool: Any) -> Providers:
    return Providers(billing=MockBilling(pool), status=MockStatusPage(pool), incidents=MockIncidents(pool))
