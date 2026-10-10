import pytest

from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW, execute, expire_lease
from tests.integration.test_operator_turns import ANSWERED, QUESTION
from tests.unit.test_rules import DRAFT

TICKET = {**request_body("t2", NOW), "ticket_id": "T-2"}
INCIDENT = call("open_incident", component="login", summary="Error 500 at login for every user of one customer")
TAKEN_BY_ANOTHER_TURN = "UPDATE conversations SET busy_turn = gen_random_uuid(), busy_until = now() + interval '2 minutes'"
LEASE_RAN_OUT = "UPDATE conversations SET busy_until = now() - interval '1 second'"


def post_ticket(client, body=TICKET):
    return client.post("/tickets", json=body)


def say(client, conversation_id):
    return client.post(f"/conversations/{conversation_id}/messages", json={"body": QUESTION})


def read(client, conversation_id):
    return client.get(f"/conversations/{conversation_id}").json()


def the_conversation(database_url):
    (row,) = execute(database_url, "SELECT id, busy_turn, busy_until > now() AS held FROM conversations")
    return row


# post the ticket with a model step the loop does not survive, as a process that died mid-turn would leave it
def die_in_the_first_turn(client, llm):
    llm.script(RuntimeError("the process died here"))
    with pytest.raises(RuntimeError):
        post_ticket(client)


def test_a_conversation_whose_turn_is_running_answers_409_and_stores_nothing(client, llm, empty_database):
    die_in_the_first_turn(client, llm)
    conversation = the_conversation(empty_database)
    one_more = {**TICKET, "messages": [*TICKET["messages"], {"author": "customer", "body": "Any news?", "sent_at": NOW.isoformat()}]}

    refusals = [post_ticket(client), post_ticket(client, one_more), say(client, conversation["id"])]

    assert conversation["held"] is True
    for refused in refusals:
        assert refused.status_code == 409
        assert refused.json()["type"].endswith("conversation-busy")
        assert refused.json()["conversation_id"] == str(conversation["id"])
    assert len(llm.requests) == 1
    stored = read(client, conversation["id"])
    assert len(stored["messages"]) == len(TICKET["messages"])
    assert [(turn["kind"], turn["status"]) for turn in stored["turns"]] == [("ingest", "running")]
    assert the_conversation(empty_database)["busy_turn"] == conversation["busy_turn"]


@pytest.mark.parametrize(
    "lost_by, step",
    [(TAKEN_BY_ANOTHER_TURN, INCIDENT), (LEASE_RAN_OUT, INCIDENT), (TAKEN_BY_ANOTHER_TURN, answer(DRAFT))],
    ids=["effect after another turn took over", "effect after the lease ran out", "decision after another turn took over"],
)
def test_a_turn_that_lost_the_conversation_fires_no_effect_and_writes_no_decision(
    client, llm, providers, empty_database, monkeypatch, lost_by, step
):
    llm.script(step)
    real = llm.respond

    def lose_the_conversation_during_the_model_call(**request):
        execute(empty_database, lost_by)
        return real(**request)

    monkeypatch.setattr(llm, "respond", lose_the_conversation_during_the_model_call)

    refused = post_ticket(client)

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("turn-lost")
    assert providers.incidents.list_open() == []
    assert execute(empty_database, "SELECT id FROM actions") == []
    assert execute(empty_database, "SELECT id FROM decisions") == []
    assert execute(empty_database, "SELECT id FROM steps WHERE kind = 'tool'") == []


def test_a_turn_holding_the_conversation_fires_its_effect_and_frees_it_with_its_decision(client, llm, providers, empty_database):
    llm.script(INCIDENT, answer(DRAFT))

    posted = post_ticket(client)

    assert posted.status_code == 201
    assert len(providers.incidents.list_open()) == 1
    assert the_conversation(empty_database)["busy_turn"] is None


def test_a_failed_operator_turn_frees_the_conversation(client, llm, empty_database):
    llm.script(answer(DRAFT), answer("not json"), answer("not json"))
    conversation_id = post_ticket(client).json()["conversation_id"]

    assert say(client, conversation_id).status_code == 503

    assert the_conversation(empty_database)["busy_turn"] is None


def test_the_next_turn_cleans_up_a_dead_ingest_turn_and_its_unfinished_incident(
    client, llm, providers, empty_database, monkeypatch
):
    pages = []

    def crash(dedup_key, **parameters):
        pages.append(dedup_key)
        raise RuntimeError("the process died during the call")

    monkeypatch.setattr(providers.incidents, "open", crash, raising=False)
    llm.script(INCIDENT)
    with pytest.raises(RuntimeError):
        post_ticket(client)
    (left,) = execute(empty_database, "SELECT status FROM actions")
    expire_lease(empty_database)
    llm.script(answer(DRAFT))

    again = post_ticket(client)

    assert left["status"] == "executing"
    assert again.status_code == 200
    conversation = read(client, again.json()["conversation_id"])
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "abandoned"), ("ingest", "completed"),
    ]
    (action,) = conversation["actions"]
    assert (action["kind"], action["status"]) == ("incident", "failed")
    assert (action["events"][-1]["type"], action["events"][-1]["detail"]["reason"]) == ("parked", "outcome_unknown")
    assert len(pages) == 1
    abandoned, triaged = conversation["decisions"]
    assert (abandoned["version"], abandoned["source"], abandoned["fallback_reason"]) == (1, "fallback", "turn_abandoned")
    assert (abandoned["next_action"], abandoned["urgency"]) == ("escalate_to_human", None)
    assert abandoned["actions"] == [{"id": action["id"], "kind": "incident", "status": "failed"}]
    assert (triaged["version"], triaged["source"]) == (2, "model")
    assert again.json()["decision"] == triaged


def test_a_dead_operator_turn_is_abandoned_without_a_fallback_decision(client, llm, empty_database):
    llm.script(answer(DRAFT), RuntimeError("the process died here"), answer(ANSWERED))
    conversation_id = post_ticket(client).json()["conversation_id"]
    with pytest.raises(RuntimeError):
        say(client, conversation_id)
    expire_lease(empty_database)

    said = say(client, conversation_id)

    assert said.status_code == 200
    conversation = read(client, conversation_id)
    assert [decision["version"] for decision in conversation["decisions"]] == [1, 2]
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "completed"), ("operator", "abandoned"), ("operator", "completed"),
    ]
