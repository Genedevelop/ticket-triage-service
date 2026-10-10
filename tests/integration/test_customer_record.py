from backend.api.runner import build_runner
from backend.application import conversations, customer, triage_turn
from backend.infrastructure.llm.scripted import answer
from tests.conftest import NOW, execute
from tests.unit.test_rules import DRAFT

MESSAGE = {"id": "m1", "author": "customer", "body": "Dark mode does not follow my system.", "sent_at": NOW}


def ingest(client, customer):
    return triage_turn.ingest(build_runner(client.app.state), NOW, ticket_id=None, customer=customer, messages=[MESSAGE])


def stored(database_url):
    (row,) = execute(database_url, "SELECT customer_id, customer_metadata FROM conversations")
    return row["customer_id"], row["customer_metadata"]


def test_ingest_stores_the_customer_id_apart_from_the_rest_of_the_customer_record(client, llm, empty_database):
    llm.script(answer(DRAFT))

    outcome = ingest(client, {"id": "cus_1", "plan": "pro", "region": "eu"})

    assert stored(empty_database) == ("cus_1", {"plan": "pro", "region": "eu"})
    assert conversations.read(client.app.state.pool, outcome.conversation_id)["customer"] == {
        "id": "cus_1", "plan": "pro", "region": "eu",
    }


def test_a_customer_record_with_only_an_id_and_a_ticket_without_a_customer_are_told_apart(client, llm, empty_database):
    llm.script(answer(DRAFT), answer(DRAFT))

    with_id = ingest(client, {"id": "cus_1"})
    assert stored(empty_database) == ("cus_1", {})
    execute(empty_database, "TRUNCATE conversations CASCADE")
    without = ingest(client, None)

    assert stored(empty_database) == (None, None)
    assert with_id.is_created and without.is_created
    read = conversations.read(client.app.state.pool, without.conversation_id)
    assert read["customer"] == {"id": None}
    assert "customer_id" not in read and "customer_metadata" not in read
