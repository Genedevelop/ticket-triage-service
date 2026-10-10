def test_healthz_reports_the_database_is_reachable(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True}


def test_healthz_answers_503_while_the_database_cannot_be_reached(client):
    client.app.state.pool.close()

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": False}
