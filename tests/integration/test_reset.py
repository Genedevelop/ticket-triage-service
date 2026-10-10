from psycopg.conninfo import conninfo_to_dict

from backend.cli import reset as reset_command
from backend.infrastructure.postgres import db
from backend.infrastructure.postgres.reset import reset
from tests.conftest import TEST_DATABASE, execute


def columns(database_url):
    rows = execute(database_url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'conversations'")
    return {row["column_name"] for row in rows}


def test_a_reset_drops_every_row_and_rebuilds_the_tables_from_the_schema_file(client, empty_database):
    execute(empty_database, "INSERT INTO conversations (ticket_id) VALUES ('T-1')")
    execute(empty_database, "ALTER TABLE conversations ADD COLUMN left_over text")
    assert execute(empty_database, "SELECT count(*) AS n FROM kb_articles")[0]["n"] > 0

    pool = db.open_pool(empty_database)
    try:
        reset(pool)
    finally:
        pool.close()

    assert execute(empty_database, "SELECT count(*) AS n FROM conversations")[0]["n"] == 0
    assert execute(empty_database, "SELECT count(*) AS n FROM kb_articles")[0]["n"] == 0
    assert "left_over" not in columns(empty_database)
    assert "busy_turn" in columns(empty_database)


def test_the_reset_command_rebuilds_the_database_it_is_configured_with_and_says_what_to_do_next(
    client, empty_database, monkeypatch, capsys
):
    execute(empty_database, "INSERT INTO conversations (ticket_id) VALUES ('T-1')")
    execute(empty_database, "ALTER TABLE conversations ADD COLUMN left_over text")
    assert conninfo_to_dict(empty_database)["dbname"] == TEST_DATABASE
    monkeypatch.setenv("DATABASE_URL", empty_database)

    exit_code = reset_command.main()

    assert exit_code == 0
    assert capsys.readouterr().out == "the database is empty and has the current schema; restart the service to load the seed data\n"
    assert execute(empty_database, "SELECT count(*) AS n FROM conversations")[0]["n"] == 0
    assert execute(empty_database, "SELECT count(*) AS n FROM kb_articles")[0]["n"] == 0
    assert "left_over" not in columns(empty_database)
    assert "busy_turn" in columns(empty_database)
