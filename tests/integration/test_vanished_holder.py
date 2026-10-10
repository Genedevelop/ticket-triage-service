# the rounds a proposal and an incident take when the row that refused their insert is gone before they can read it
from contextlib import contextmanager

import pytest

from backend.core.errors import Internal
from backend.domain import action_status as machine
from backend.infrastructure.postgres import actions as store
from backend.infrastructure.postgres import incident_fixtures
from tests.conftest import execute

KEY = "refund:ch_vanishing"
EVERY_ROUND = 99


@pytest.fixture()
def pool(client):
    return client.app.state.pool


@pytest.fixture()
def conversation_id(empty_database, client):
    return execute(empty_database, "INSERT INTO conversations (customer_id) VALUES ('cus_vanishing') RETURNING id")[0]["id"]


# make the first inserts return and leave no row, as when the key's holder vanished; yields the count of tries
@contextmanager
def holder_vanishing(database_url, table, times):
    execute(database_url, "CREATE SEQUENCE tried_inserts")
    execute(
        database_url,
        f"""
        CREATE FUNCTION refuse_first_inserts() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF nextval('tried_inserts') <= {int(times)} THEN RETURN NULL; END IF;
            RETURN NEW;
        END $$
        """,
    )
    execute(database_url, f"CREATE TRIGGER refuse_first_inserts BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION refuse_first_inserts()")
    try:
        yield lambda: execute(database_url, "SELECT CASE WHEN is_called THEN last_value ELSE 0 END AS n FROM tried_inserts")[0]["n"]
    finally:
        execute(database_url, f"DROP TRIGGER refuse_first_inserts ON {table}")
        execute(database_url, "DROP FUNCTION refuse_first_inserts")
        execute(database_url, "DROP SEQUENCE tried_inserts")


def propose(pool, conversation_id):
    return store.propose(
        pool, kind="refund", key=KEY, is_key_held=True, status=machine.PENDING, payload={}, evidence={}, actor="model",
        conversation_id=conversation_id, detail={"reason": "charged twice"},
    )


def open_incident(providers):
    return providers.incidents.open("login:asia", component="login", region="asia", summary="500 at login", timeout_s=1)


def test_a_proposal_whose_key_was_held_and_is_free_at_the_read_looks_again_and_takes_the_key(pool, conversation_id, empty_database):
    with holder_vanishing(empty_database, "actions", times=1) as tried:
        action, is_created = propose(pool, conversation_id)
        assert tried() == 2

    assert is_created is True
    assert execute(empty_database, "SELECT id, status FROM actions") == [{"id": action["id"], "status": "pending_approval"}]
    assert [event["type"] for event in store.get(pool, action["id"])["events"]] == ["proposed"]


def test_a_proposal_whose_key_is_neither_free_nor_held_in_any_round_gives_up_as_an_internal_error_and_writes_nothing(
    pool, conversation_id, empty_database
):
    with holder_vanishing(empty_database, "actions", times=EVERY_ROUND) as tried:
        with pytest.raises(Internal) as gave_up:
            propose(pool, conversation_id)
        assert tried() == store.PROPOSE_ROUNDS == 3

    assert type(gave_up.value) is Internal
    assert gave_up.value.message == f"the key {KEY} was neither free nor held after 3 rounds"
    assert execute(empty_database, "SELECT count(*) AS n FROM actions")[0]["n"] == 0
    assert execute(empty_database, "SELECT count(*) AS n FROM action_events")[0]["n"] == 0


def test_an_incident_whose_open_twin_is_resolved_before_the_read_is_opened_on_the_next_round(providers, empty_database, client):
    with holder_vanishing(empty_database, "mock_incidents", times=1) as tried:
        opened = open_incident(providers)
        assert tried() == 2

    assert opened["already_open"] is False
    assert [row["incident_id"] for row in providers.incidents.list_open()] == [opened["incident_id"]]


def test_an_incident_that_is_neither_opened_nor_found_open_in_any_round_gives_up_as_an_internal_error(providers, empty_database, client):
    with holder_vanishing(empty_database, "mock_incidents", times=EVERY_ROUND) as tried:
        with pytest.raises(Internal) as gave_up:
            open_incident(providers)
        assert tried() == incident_fixtures.OPEN_ROUNDS == 3

    assert gave_up.value.message == "the dedup key login:asia was neither free nor open after 3 rounds"
    assert providers.incidents.list_open() == []
