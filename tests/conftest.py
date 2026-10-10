# test fixtures: every run gets a freshly created database of its own, never the served one
import os
from datetime import datetime, timezone

import psycopg
import psycopg.rows
import pytest
from fastapi.testclient import TestClient
from hypothesis import settings as property_settings
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.core.config import load_settings
from backend.infrastructure.llm.scripted import ScriptedLLM
from backend.infrastructure.postgres import db
from backend.infrastructure.providers import mock_providers
from backend.infrastructure.providers.sample_data import load_seed
from main import create_app

TEST_DATABASE = "triage_test"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

# property tests draw the same examples on every run, keep no example database and have no time limit per example
property_settings.register_profile("repeatable", derandomize=True, database=None, deadline=None, max_examples=100)
property_settings.load_profile("repeatable")


# drop and recreate the test database, and return its URL
@pytest.fixture(scope="session")
def database_url() -> str:
    served = os.environ["DATABASE_URL"]
    if conninfo_to_dict(served).get("dbname") == TEST_DATABASE:
        raise RuntimeError("the served database must not be the test database")
    with psycopg.connect(served, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DATABASE}"')
    return make_conninfo(served, dbname=TEST_DATABASE)


# remove every row of every table, keeping the tables
def truncate_every_table(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        if tables:
            conn.execute("TRUNCATE " + ", ".join(f'"{table}"' for table in tables) + " RESTART IDENTITY CASCADE")


# empty every table, so no test sees another test's rows
@pytest.fixture()
def empty_database(database_url: str) -> str:
    truncate_every_table(database_url)
    return database_url


# the scripted model; a test sets its script before posting
@pytest.fixture()
def llm() -> ScriptedLLM:
    return ScriptedLLM()


# mock providers on their own connections to the test database
@pytest.fixture()
def providers(empty_database: str):
    pool = db.open_pool(empty_database)
    try:
        yield mock_providers(pool)
    finally:
        pool.close()


# the application on the test database, driven in process with a fixed clock and the sample customers loaded into the mocks
@pytest.fixture()
def client(empty_database: str, llm: ScriptedLLM, providers):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    app = create_app(llm=llm, providers=providers, clock=lambda: NOW, settings=settings, load_sample_data=load_seed)
    with TestClient(app) as test_client:
        yield test_client


# run one statement on the test database from outside the application, and return its rows if it has any
def execute(database_url: str, query: str, *parameters):
    with psycopg.connect(database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        cur = conn.execute(query, parameters)
        return cur.fetchall() if cur.description else []


# let every conversation's lease run out by moving its end into the past, instead of waiting for it
def expire_lease(database_url: str) -> None:
    execute(database_url, "UPDATE conversations SET busy_until = now() - interval '1 second' WHERE busy_turn IS NOT NULL")
