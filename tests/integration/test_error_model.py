# the one error model through the http client: an error raised in a route is answered once, by the central handler
import os

import pytest
from fastapi.testclient import TestClient

from backend.core import errors
from backend.core.config import load_settings
from main import create_app
from tests.unit.test_error_model import PROBLEM


# the application with one route per failure, answering instead of re-raising
@pytest.fixture()
def failing(empty_database, llm, providers):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    app = create_app(llm=llm, providers=providers, clock=None, settings=settings)

    @app.get("/boom/unknown")
    def unknown():
        raise KeyError("secret-table-name")

    @app.get("/boom/internal")
    def internal():
        raise errors.Internal("secret-illegal-state", details={"secret": "detail"})

    @app.get("/boom/conflict")
    def conflict():
        raise errors.Conflict("already taken", details={"key": "k1"})

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize("path", ["/boom/unknown", "/boom/internal"])
def test_a_failure_of_the_service_itself_answers_a_plain_500_without_leaking_internals(failing, path):
    response = failing.get(path)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Internal Server Error"


def test_an_error_raised_in_a_route_is_answered_by_the_central_handler(failing):
    response = failing.get("/boom/conflict")

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM
    assert response.json() == {
        "type": "urn:ticket-triage:problem:conflict", "title": "Conflict", "status": 409, "detail": "already taken", "key": "k1",
    }


def test_a_failure_of_the_service_itself_still_reaches_the_server(empty_database, llm, providers):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    app = create_app(llm=llm, providers=providers, clock=None, settings=settings)

    @app.get("/boom/internal")
    def internal():
        raise errors.Internal("illegal state")

    with TestClient(app) as client, pytest.raises(errors.Internal):
        client.get("/boom/internal")
