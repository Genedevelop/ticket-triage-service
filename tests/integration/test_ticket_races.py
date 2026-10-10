import threading
import time

import psycopg
import pytest

from backend.api.runner import build_runner
from backend.application import triage_turn
from backend.core.errors import OtherCustomer
from backend.infrastructure.llm.scripted import answer
from backend.infrastructure.postgres import conversations as store
from tests.conftest import NOW, execute
from tests.integration.test_refund_races import together
from tests.unit.test_rules import DRAFT

ROUNDS = 15
CUSTOMERS = ("cus_one", "cus_other")


def message(customer_id):
    return {"author": "customer", "body": f"I am {customer_id} and my export fails.", "sent_at": NOW}


def sender(client, ticket_id, customer_id):
    return lambda: triage_turn.ingest(
        build_runner(client.app.state), NOW, ticket_id=ticket_id, customer={"id": customer_id}, messages=[message(customer_id)]
    )


def test_two_customers_racing_on_one_new_ticket_id_give_one_conversation_and_one_mismatch(client, llm, empty_database):
    for number in range(ROUNDS):
        ticket_id = f"T-race-{number}"
        llm.script(answer(DRAFT))

        outcomes = together(*(sender(client, ticket_id, customer_id) for customer_id in CUSTOMERS))

        refused = [outcome for outcome in outcomes if isinstance(outcome, OtherCustomer)]
        created = [outcome for outcome in outcomes if isinstance(outcome, triage_turn.Outcome)]
        assert (len(created), len(refused)) == (1, 1), outcomes
        assert created[0].is_created
        assert refused[0].details == {"ticket_id": ticket_id}
        winner = CUSTOMERS[outcomes.index(created[0])]
        assert execute(empty_database, "SELECT id, customer_id FROM conversations WHERE ticket_id = %s", ticket_id) == [
            {"id": created[0].conversation_id, "customer_id": winner}
        ]
        assert execute(empty_database, "SELECT body FROM messages WHERE conversation_id = %s", created[0].conversation_id) == [
            {"body": message(winner)["body"]}
        ]
        assert execute(empty_database, "SELECT count(*) AS n FROM turns WHERE conversation_id = %s", created[0].conversation_id) == [
            {"n": 1}
        ]
    assert execute(empty_database, "SELECT count(*) AS n FROM conversations") == [{"n": ROUNDS}]
    assert len(llm.requests) == ROUNDS


@pytest.mark.parametrize("first_commits", [True, False])
def test_another_customers_ticket_waits_for_an_uncommitted_holder_of_its_ticket_id(client, empty_database, first_commits):
    result = []

    def send_late():
        try:
            result.append(
                store.start_ingest(
                    client.app.state.pool, ticket_id="T-held", customer_id="cus_late", customer_metadata=None,
                    messages=[message("cus_late")], lease_s=60, fallback=lambda steps, actions, previous: {},
                )
            )
        except OtherCustomer as error:
            result.append(error)

    with psycopg.connect(empty_database) as first:
        first.execute("INSERT INTO conversations (ticket_id, customer_id) VALUES ('T-held', 'cus_holder')")
        second = threading.Thread(target=send_late)
        second.start()
        deadline = time.monotonic() + 10
        waiting = []
        while not waiting and time.monotonic() < deadline:
            waiting = execute(
                empty_database,
                "SELECT pid FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE '%%INSERT INTO conversations%%'",
            )
            time.sleep(0.02)
        assert waiting, "the second insert did not wait for the first"
        assert result == []
        if first_commits:
            first.commit()
        else:
            first.rollback()
    second.join(timeout=10)

    (outcome,) = result
    stored = execute(empty_database, "SELECT customer_id FROM conversations WHERE ticket_id = 'T-held'")
    messages = execute(empty_database, "SELECT count(*) AS n FROM messages")
    if first_commits:
        assert isinstance(outcome, OtherCustomer)
        assert (stored, messages) == ([{"customer_id": "cus_holder"}], [{"n": 0}])
    else:
        assert outcome.is_created
        assert (stored, messages) == ([{"customer_id": "cus_late"}], [{"n": 1}])
