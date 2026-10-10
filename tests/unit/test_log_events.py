import json
import logging

import pytest

from backend.core import logging as log
from backend.core.errors import Misconfigured


# every line the service logged, parsed
def events(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == log.LOGGER]


def test_the_debug_level_adds_the_payload(caplog):
    log.configure("DEBUG")
    try:
        log.event("tool.call", outcome="ok", payload={"arguments": {"charge_id": "ch_1"}})
    finally:
        log.configure("INFO")
    log.event("tool.call", outcome="ok", payload={"arguments": {"charge_id": "ch_1"}})

    with_payload, without = events(caplog)
    assert with_payload["payload"] == {"arguments": {"charge_id": "ch_1"}}
    assert "payload" not in without


def test_an_unknown_log_level_is_refused():
    log.configure("INFO")
    with pytest.raises(log.UnknownLogLevel) as refused:
        log.configure("LOUD")
    assert isinstance(refused.value, Misconfigured)
    assert refused.value.message == "Unknown level: 'LOUD'"
    assert logging.getLogger(log.LOGGER).level == logging.INFO


def test_a_level_above_info_writes_no_event(caplog):
    log.configure("WARNING")
    try:
        log.event("tool.call", outcome="ok", payload={"arguments": {"charge_id": "ch_1"}})
    finally:
        log.configure("INFO")
    log.event("decision.made", outcome="ok")

    assert [line["event"] for line in events(caplog)] == ["decision.made"]
