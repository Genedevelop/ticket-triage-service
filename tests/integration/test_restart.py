import os

import pytest
from fastapi.testclient import TestClient

from backend.core.config import load_settings
from backend.domain.ports import Providers
from backend.infrastructure.llm.scripted import ScriptedLLM, answer, call
from evals.sample import request_body
from main import create_app
from tests.conftest import NOW, execute
from tests.integration.test_operator_turns import ANSWERED, QUESTION
from tests.unit.test_rules import DRAFT

TICKET = {**request_body("t1", NOW), "ticket_id": "T-1"}


# A second application on the same database, as the service is after a restart
def restarted(database_url, providers, llm):
    settings = load_settings({**os.environ, "DATABASE_URL": database_url, "OPENAI_API_KEY": ""})
    return TestClient(create_app(llm=llm, providers=providers, clock=lambda: NOW, settings=settings))


def test_everything_stored_is_read_back_by_a_second_application_on_the_same_database(client, llm, providers, empty_database):
    llm.script(call("billing_lookup"), call("propose_refund", charge_id="ch_t1_1", reason="charged twice"), answer(DRAFT), answer(ANSWERED))
    conversation_id = client.post("/tickets", json=TICKET).json()["conversation_id"]
    client.post(f"/conversations/{conversation_id}/messages", json={"body": QUESTION})
    before = client.get(f"/conversations/{conversation_id}").json()

    second_llm = ScriptedLLM()
    with restarted(empty_database, providers, second_llm) as second:
        after = second.get(f"/conversations/{conversation_id}").json()
        replayed = second.post("/tickets", json=TICKET)

    assert after == before
    assert [len(after[part]) for part in ("messages", "decisions", "actions", "turns")] == [6, 2, 1, 2]
    assert replayed.headers["Idempotent-Replayed"] == "true"
    assert replayed.json()["decision"] == before["decisions"][-1]
    assert second_llm.requests == []


def test_start_up_cleans_up_the_turn_a_dead_process_left_and_frees_its_conversation(client, llm, providers, empty_database):
    llm.script(RuntimeError("the process died here"))
    with pytest.raises(RuntimeError):
        client.post("/tickets", json=TICKET)
    (left,) = execute(empty_database, "SELECT id, busy_until > now() AS held FROM conversations")

    second_llm = ScriptedLLM()
    second_llm.script(answer(DRAFT))
    with restarted(empty_database, providers, second_llm) as second:
        found = second.get(f"/conversations/{left['id']}").json()
        again = second.post("/tickets", json=TICKET)

    assert left["held"] is True
    assert [(turn["kind"], turn["status"]) for turn in found["turns"]] == [("ingest", "abandoned")]
    (decision,) = found["decisions"]
    assert (decision["version"], decision["source"], decision["fallback_reason"]) == (1, "fallback", "turn_abandoned")
    assert decision["next_action"] == "escalate_to_human"
    assert again.status_code == 200
    assert (again.json()["decision"]["version"], again.json()["decision"]["source"]) == (2, "model")


def test_start_up_loads_the_sample_data_through_a_loader_the_caller_can_replace(empty_database, providers, llm):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    loaded = []
    app = create_app(
        llm=llm, providers=providers, clock=lambda: NOW, settings=settings,
        load_sample_data=lambda given, now: loaded.append((given, now)),
    )

    with TestClient(app) as started:
        assert started.get("/healthz").status_code == 200

    assert loaded == [(providers, NOW)]
    assert execute(empty_database, "SELECT count(*) AS n FROM mock_charges")[0]["n"] == 0


def test_start_up_with_the_callers_own_providers_and_no_loader_loads_no_sample_data(empty_database, llm):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    not_mocks = Providers(billing=object(), status=object(), incidents=object())
    app = create_app(llm=llm, providers=not_mocks, clock=lambda: NOW, settings=settings)

    with TestClient(app) as started:
        assert started.get("/healthz").status_code == 200

    assert app.state.providers is not_mocks
    assert execute(empty_database, "SELECT count(*) AS n FROM mock_charges")[0]["n"] == 0
