import pytest

from backend.domain.ports import LLMUnavailable
from backend.infrastructure.llm.scripted import answer
from evals.sample import request_body
from tests.conftest import NOW, expire_lease
from tests.integration.test_operator_turns import ANSWERED, QUESTION
from tests.integration.test_problem_json import assert_problem
from tests.unit.test_rules import DRAFT

TICKET = {**request_body("t1", NOW), "ticket_id": "T-1"}


def post_ticket(client, body=TICKET):
    return client.post("/tickets", json=body)


def say(client, conversation_id, body=QUESTION, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/conversations/{conversation_id}/messages", json={"body": body}, headers=headers)


def read(client, conversation_id):
    return client.get(f"/conversations/{conversation_id}").json()


def test_a_ticket_sent_again_gets_its_stored_decision_without_a_model_call(client, llm):
    llm.script(answer(DRAFT))
    first = post_ticket(client)

    again = post_ticket(client)

    assert (first.status_code, again.status_code) == (201, 200)
    assert "Idempotent-Replayed" not in first.headers
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert len(llm.requests) == 1
    conversation = read(client, first.json()["conversation_id"])
    assert len(conversation["messages"]) == 4
    assert len(conversation["turns"]) == 1
    assert len(conversation["decisions"]) == 1


def test_a_ticket_sent_again_with_one_more_message_is_triaged_again(client, llm):
    llm.script(answer(DRAFT), answer({**DRAFT, "urgency": "critical"}))
    post_ticket(client, {**TICKET, "messages": TICKET["messages"][:3]})

    continued = post_ticket(client)

    assert continued.status_code == 200
    assert "Idempotent-Replayed" not in continued.headers
    assert (continued.json()["decision"]["version"], continued.json()["decision"]["urgency"]) == (2, "critical")


def test_a_ticket_whose_triage_fell_back_is_triaged_again(client, llm):
    llm.script(LLMUnavailable("APITimeoutError"), LLMUnavailable("APITimeoutError"), answer(DRAFT))
    first = post_ticket(client)

    again = post_ticket(client)

    assert (first.status_code, first.json()["decision"]["source"]) == (201, "fallback")
    assert again.status_code == 200
    assert "Idempotent-Replayed" not in again.headers
    assert (again.json()["decision"]["version"], again.json()["decision"]["source"]) == (2, "model")
    assert len(llm.requests) == 3


def test_a_ticket_whose_first_attempt_died_is_triaged_again(client, llm, empty_database):
    llm.script(RuntimeError("the process died here"), answer(DRAFT))
    with pytest.raises(RuntimeError):
        post_ticket(client)
    expire_lease(empty_database)

    again = post_ticket(client)

    assert again.status_code == 200
    assert "Idempotent-Replayed" not in again.headers
    assert again.json()["decision"]["source"] == "model"
    conversation = read(client, again.json()["conversation_id"])
    assert len(conversation["messages"]) == 4
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "abandoned"), ("ingest", "completed"),
    ]


def test_a_ticket_sent_again_after_an_operator_turn_gets_the_latest_decision(client, llm):
    llm.script(answer(DRAFT), answer(ANSWERED))
    conversation_id = post_ticket(client).json()["conversation_id"]
    said = say(client, conversation_id)

    again = post_ticket(client)

    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["decision"] == said.json()["decision"]
    assert len(llm.requests) == 2


def test_an_operator_message_with_the_same_key_is_stored_once_and_answered_from_its_turn(client, llm):
    llm.script(answer(DRAFT), answer(ANSWERED))
    conversation_id = post_ticket(client).json()["conversation_id"]
    first = say(client, conversation_id, key="op-1")

    again = say(client, conversation_id, key="op-1")

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json() == first.json()
    assert again.json()["decision"]["version"] == 2
    assert len(llm.requests) == 2
    conversation = read(client, conversation_id)
    assert [message["author"] for message in conversation["messages"]].count("operator") == 1
    assert [turn["kind"] for turn in conversation["turns"]] == ["ingest", "operator"]


def test_an_operator_message_whose_turn_never_finished_runs_again_under_the_same_key(client, llm):
    llm.script(answer(DRAFT), LLMUnavailable("APITimeoutError"), LLMUnavailable("APITimeoutError"), answer(ANSWERED))
    conversation_id = post_ticket(client).json()["conversation_id"]
    refused = say(client, conversation_id, key="op-1")

    again = say(client, conversation_id, key="op-1")

    assert (refused.status_code, again.status_code) == (503, 200)
    assert again.json()["decision"]["version"] == 2
    conversation = read(client, conversation_id)
    assert [message["author"] for message in conversation["messages"]].count("operator") == 1
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "completed"), ("operator", "failed"), ("operator", "completed"),
    ]


def test_the_same_words_under_another_key_or_under_none_are_new_turns(client, llm):
    llm.script(answer(DRAFT), answer(ANSWERED), answer(ANSWERED), answer(ANSWERED))
    conversation_id = post_ticket(client).json()["conversation_id"]

    versions = [
        say(client, conversation_id, key=key).json()["decision"]["version"] for key in ("op-1", "op-2", None)
    ]

    assert versions == [2, 3, 4]


def with_customer(customer_id, body=TICKET):
    customer = {key: value for key, value in body["customer"].items() if key != "id"}
    return {**body, "customer": {**customer, "id": customer_id} if customer_id else customer}


def test_a_ticket_sent_again_under_another_customer_is_refused_and_nothing_is_stored(client, llm):
    llm.script(answer(DRAFT))
    first = post_ticket(client)
    late = {"author": "customer", "body": "Refund my last charge.", "sent_at": NOW.isoformat()}
    other = with_customer("cus_someone_else", {**TICKET, "messages": [*TICKET["messages"], late]})

    refused = post_ticket(client, other)

    body = assert_problem(refused, 409, ":ticket-customer-mismatch", "Ticket belongs to another customer")
    assert body["ticket_id"] == "T-1"
    assert set(body) == {"type", "title", "status", "detail", "ticket_id"}
    assert len(llm.requests) == 1
    conversation = read(client, first.json()["conversation_id"])
    assert conversation["customer"]["id"] == TICKET["customer"]["id"]
    assert len(conversation["messages"]) == 4
    assert len(conversation["turns"]) == 1
    assert len(conversation["decisions"]) == 1


def test_a_ticket_sent_again_without_a_customer_id_continues_its_conversation(client, llm):
    llm.script(answer(DRAFT))
    first = post_ticket(client)

    again = post_ticket(client, with_customer(None))

    assert again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert read(client, first.json()["conversation_id"])["customer"]["id"] == TICKET["customer"]["id"]


def test_a_ticket_stored_without_a_customer_id_continues_when_one_is_sent_later(client, llm):
    llm.script(answer(DRAFT))
    first = post_ticket(client, with_customer(None))

    again = post_ticket(client)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert read(client, first.json()["conversation_id"])["customer"]["id"] is None
