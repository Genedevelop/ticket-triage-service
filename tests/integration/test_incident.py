from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.application.actions import incident as incident_effect
from backend.application.actions.execute import Timing
from backend.application.tools.registry import run_tool
from backend.application.tools.tool import ToolContext
from backend.domain.ports import ProviderError, ToolCall
from backend.infrastructure.llm.scripted import answer, call
from backend.infrastructure.postgres import actions as store
from evals.sample import request_body
from tests.conftest import NOW, execute
from tests.unit.test_incident import ROUTED

# the region on the second sample customer's record
REGION = "thailand"


def incident(component="login", summary="Error 500 at login for every user of one customer"):
    return call("open_incident", component=component, summary=summary)


def post_ticket(client, llm, *steps, body=None):
    llm.script(*steps, answer(ROUTED))
    posted = client.post("/tickets", json=body or request_body("t2", NOW))
    assert posted.status_code == 201
    return posted.json()


def say(client, llm, conversation_id, *steps):
    llm.script(*steps, answer(ROUTED))
    said = client.post(f"/conversations/{conversation_id}/messages", json={"body": "Is it still down?"})
    assert said.status_code == 200
    return said.json()


def tool_steps(client, conversation_id, name="open_incident", turn=-1):
    turns = client.get(f"/conversations/{conversation_id}").json()["turns"]
    return [step for step in turns[turn]["steps"] if step["name"] == name]


def rows(database_url, query, *parameters):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        return conn.execute(query, parameters).fetchall()


# the actions that hold their key at this moment
def holders(database_url):
    return rows(
        database_url,
        """
        SELECT id FROM actions
         WHERE status IN ('pending_approval', 'approved', 'executing', 'needs_review')
            OR (hold_key AND status IN ('succeeded', 'rejected'))
        """,
    )


def rule_names(decision):
    return [override["rule"] for override in decision["overrides"]]


# the executor goes round again at once instead of waiting between attempts
@pytest.fixture()
def no_waits(client):
    client.app.state.action_timing = Timing(waits_s=(0, 0))


# every call that reaches the incident system, each answered by the next scripted fault or by the mock
@pytest.fixture()
def pager_calls(providers, monkeypatch):
    calls = []
    faults = []
    real = providers.incidents.open

    def counted(dedup_key, **parameters):
        calls.append(dedup_key)
        fault = faults.pop(0) if faults else None
        if fault == "fail_before":
            raise ProviderError("timeout", "the incident system did not answer", is_retryable=True)
        answered = real(dedup_key, **parameters)
        if fault == "lose_answer":
            raise ProviderError("timeout", "the incident system did not answer", is_retryable=True)
        return answered

    monkeypatch.setattr(providers.incidents, "open", counted, raising=False)
    return calls, faults


def test_the_mock_attaches_to_an_incident_while_it_is_open_and_opens_a_new_one_after_resolution(providers):
    first = providers.incidents.open("login:asia", component="login", region="asia", summary="500 at login", timeout_s=1)
    again = providers.incidents.open("login:asia", component="login", region="asia", summary="cannot log in", timeout_s=1)
    other = providers.incidents.open("api:asia", component="api", region="asia", summary="500 from the API", timeout_s=1)

    assert (first["already_open"], again["already_open"], other["already_open"]) == (False, True, False)
    assert again["incident_id"] == first["incident_id"] != other["incident_id"]

    providers.incidents.resolve(first["incident_id"])
    recurred = providers.incidents.open("login:asia", component="login", region="asia", summary="500 again", timeout_s=1)

    assert recurred["already_open"] is False
    assert recurred["incident_id"] != first["incident_id"]
    assert sorted(row["incident_id"] for row in providers.incidents.list_open()) == sorted(
        [other["incident_id"], recurred["incident_id"]]
    )


def test_an_opened_incident_is_executed_in_line_and_sends_the_ticket_to_a_person(client, llm, providers, empty_database):
    posted = post_ticket(client, llm, incident(), call("status_check"))

    conversation_id = posted["conversation_id"]
    decision = posted["decision"]
    assert [(action["kind"], action["status"]) for action in decision["actions"]] == [("incident", "succeeded")]
    assert decision["next_action"] == "escalate_to_human"
    assert rule_names(decision) == ["R2"]
    (step,) = tool_steps(client, conversation_id)
    assert step["ok"] is True
    (opened,) = providers.incidents.list_open()
    assert step["result"]["incident"] == {"id": opened["incident_id"], "already_existed": False}
    assert (opened["dedup_key"], opened["component"], opened["region"]) == (f"login:{REGION}", "login", REGION)
    (action,) = client.get(f"/conversations/{conversation_id}").json()["actions"]
    assert action["key"] == f"incident:{conversation_id}:login:{REGION}"
    assert (action["hold_key"], action["attempts"], action["provider_ref"]) == (False, 1, opened["incident_id"])
    assert [(event["type"], event["actor"]) for event in action["events"]] == [
        ("proposed", "model"), ("approved", "policy:incident"), ("claimed", "executor"), ("provider_result", "executor"),
    ]
    (checked,) = tool_steps(client, conversation_id, "status_check")
    assert [row["incident_id"] for row in checked["result"]["open_incidents"]] == [opened["incident_id"]]
    assert holders(empty_database) == []


def test_a_ticket_that_joins_an_open_incident_is_not_sent_to_a_person(client, llm, providers):
    first = post_ticket(client, llm, incident())
    second = post_ticket(client, llm, incident(summary="Colleagues cannot log in either"))

    assert first["conversation_id"] != second["conversation_id"]
    assert first["decision"]["next_action"] == "escalate_to_human"
    assert second["decision"]["next_action"] == "route_to_specialist"
    assert second["decision"]["overrides"] == []
    assert [(action["kind"], action["status"]) for action in second["decision"]["actions"]] == [("incident", "succeeded")]
    (opened,) = providers.incidents.list_open()
    (step,) = tool_steps(client, second["conversation_id"])
    assert step["result"]["incident"] == {"id": opened["incident_id"], "already_existed": True}


def test_a_second_incident_in_one_turn_is_denied(client, llm, providers, pager_calls):
    calls, _ = pager_calls

    posted = post_ticket(client, llm, incident("login"), incident("api"), incident("login"))

    first, second, third = [step["result"] for step in tool_steps(client, posted["conversation_id"])]
    assert first["denied"] is False
    assert (second["denied"], second["reason"]) == (True, "one_incident_per_turn")
    assert (third["denied"], third["reason"]) == (True, "one_incident_per_turn")
    assert calls == [f"login:{REGION}"]
    assert len(providers.incidents.list_open()) == 1
    assert len(posted["decision"]["actions"]) == 1


def test_three_failed_attempts_leave_the_incident_failed_and_send_the_ticket_to_a_person(
    client, llm, providers, empty_database, pager_calls, no_waits
):
    calls, faults = pager_calls
    faults.extend(["fail_before"] * 3)

    posted = post_ticket(client, llm, incident())

    assert calls == [f"login:{REGION}"] * 3
    assert providers.incidents.list_open() == []
    decision = posted["decision"]
    assert [(action["kind"], action["status"]) for action in decision["actions"]] == [("incident", "failed")]
    assert decision["next_action"] == "escalate_to_human"
    assert rule_names(decision) == ["R2"]
    (step,) = tool_steps(client, posted["conversation_id"])
    assert step["ok"] is False
    assert step["result"]["error"]["type"] == "incident_not_opened"
    (action,) = client.get(f"/conversations/{posted['conversation_id']}").json()["actions"]
    assert action["attempts"] == 3
    assert [event["type"] for event in action["events"]][-1] == "parked"
    assert holders(empty_database) == []


def test_a_first_attempt_that_paged_and_lost_the_answer_still_counts_as_the_one_that_opened_it(
    client, llm, providers, pager_calls, no_waits
):
    calls, faults = pager_calls
    faults.append("lose_answer")

    posted = post_ticket(client, llm, incident())

    assert len(calls) == 2
    assert len(providers.incidents.list_open()) == 1
    (step,) = tool_steps(client, posted["conversation_id"])
    assert step["result"]["incident"]["already_existed"] is True
    assert [(action["status"]) for action in posted["decision"]["actions"]] == ["succeeded"]
    assert posted["decision"]["next_action"] == "escalate_to_human"
    assert rule_names(posted["decision"]) == ["R2"]


def test_the_key_is_free_after_success_so_a_later_turn_joins_and_a_recurrence_pages_again(client, llm, providers, empty_database):
    posted = post_ticket(client, llm, incident())
    conversation_id = posted["conversation_id"]
    (opened,) = providers.incidents.list_open()

    quiet = say(client, llm, conversation_id)
    joined = say(client, llm, conversation_id, incident())

    # an incident of an earlier turn and one that was plainly joined set no floor
    assert quiet["decision"]["next_action"] == "route_to_specialist"
    assert joined["decision"]["next_action"] == "route_to_specialist"
    assert joined["decision"]["overrides"] == []
    assert tool_steps(client, conversation_id)[0]["result"]["incident"] == {"id": opened["incident_id"], "already_existed": True}

    providers.incidents.resolve(opened["incident_id"])
    recurred = say(client, llm, conversation_id, incident())

    assert recurred["decision"]["next_action"] == "escalate_to_human"
    assert rule_names(recurred["decision"]) == ["R2"]
    (reopened,) = providers.incidents.list_open()
    assert reopened["incident_id"] != opened["incident_id"]
    actions = rows(empty_database, "SELECT key, status FROM actions ORDER BY created_at")
    assert actions == [{"key": f"incident:{conversation_id}:login:{REGION}", "status": "succeeded"}] * 3


def test_a_ticket_without_a_region_opens_its_incident_for_the_global_region(client, llm, providers):
    body = request_body("t2", NOW)
    del body["customer"]["region"]

    post_ticket(client, llm, incident("payments"), body=body)

    (opened,) = providers.incidents.list_open()
    assert opened["dedup_key"] == "payments:global"


def test_a_component_outside_the_closed_list_is_refused_before_anything_is_proposed(client, llm, providers, empty_database):
    posted = post_ticket(client, llm, incident("database"))

    (step,) = tool_steps(client, posted["conversation_id"])
    assert step["ok"] is False
    assert step["result"]["error"]["type"] == "invalid_arguments"
    assert providers.incidents.list_open() == []
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 0}]


# an incident request of an earlier turn that is approved and not executed yet; returns the pool, its conversation and the action
def incident_in_flight(client, database_url):
    pool = client.app.state.pool
    conversation_id = execute(database_url, "INSERT INTO conversations (customer_id) VALUES ('cust_t2') RETURNING id")[0]["id"]
    action, is_created = incident_effect.open(
        pool, lambda action_id: store.get(pool, action_id), conversation_id=conversation_id, turn_id=uuid4(),
        component="login", region=REGION, summary="Error 500 at login", now=NOW,
    )
    assert (is_created, action["status"]) == (True, "approved")
    return pool, conversation_id, action


def test_a_request_for_an_incident_already_in_flight_joins_it_and_executes_nothing(client, providers, empty_database):
    pool, conversation_id, in_flight = incident_in_flight(client, empty_database)
    driven = []

    joined, is_created = incident_effect.open(
        pool, driven.append, conversation_id=conversation_id, turn_id=uuid4(),
        component="login", region=REGION, summary="Still error 500 at login", now=NOW,
    )

    assert is_created is False
    assert (joined["id"], joined["status"]) == (in_flight["id"], "approved")
    assert driven == []
    assert [event["type"] for event in store.get(pool, in_flight["id"])["events"]] == ["proposed", "approved", "joined"]
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 1}]
    assert providers.incidents.list_open() == []


def test_the_tool_reports_an_incident_request_still_in_flight_as_in_progress_and_pages_nobody(client, providers, empty_database):
    pool, conversation_id, in_flight = incident_in_flight(client, empty_database)
    context = ToolContext(
        conversation_id=conversation_id, customer_id="cust_t2", turn_id=uuid4(), now=NOW, providers=providers, pool=pool, region=REGION,
    )
    asked = ToolCall(call_id="call_1", name="open_incident", arguments='{"component": "login", "summary": "Still error 500 at login"}')

    _, result, is_ok = run_tool(asked, context)

    assert is_ok is True
    assert result == {
        "denied": False,
        "action": {
            "id": str(in_flight["id"]), "kind": "incident", "status": "approved",
            "component": "login", "region": REGION, "summary": "Error 500 at login",
        },
        "incident": None,
        "already_requested": True,
        "note": "This incident request is still in progress; nothing is known to be open yet.",
    }
    assert providers.incidents.list_open() == []
