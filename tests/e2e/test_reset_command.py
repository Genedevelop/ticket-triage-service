import os
import subprocess
import sys

from tests.conftest import execute
from tests.e2e.conftest import ROOT

COMMAND = [sys.executable, "-m", "backend.cli.reset"]
RUN_DEADLINE_S = 60


def tables(database_url):
    return {row["tablename"] for row in execute(database_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}


def test_the_reset_command_run_as_documented_drops_what_it_finds_and_leaves_the_empty_current_schema(empty_database):
    execute(empty_database, "CREATE TABLE left_over (note text)")
    execute(empty_database, "INSERT INTO left_over VALUES ('from an older schema')")

    ran = subprocess.run(
        COMMAND, cwd=ROOT, env={**os.environ, "DATABASE_URL": empty_database}, capture_output=True, text=True, timeout=RUN_DEADLINE_S
    )

    assert (ran.returncode, ran.stderr) == (0, "")
    assert ran.stdout == "the database is empty and has the current schema; restart the service to load the seed data\n"
    assert "left_over" not in tables(empty_database)
    assert {"conversations", "messages", "turns", "steps", "decisions", "actions", "action_events", "kb_articles"} <= tables(empty_database)
    assert execute(empty_database, "SELECT count(*) AS n FROM kb_articles")[0]["n"] == 0
