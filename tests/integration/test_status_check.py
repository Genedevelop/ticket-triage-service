from datetime import datetime

from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT


def test_the_status_page_is_green_with_its_age_and_no_incident_is_open(client, llm):
    llm.script(call("status_check"), answer(DRAFT))

    posted = client.post("/tickets", json=request_body("t2", NOW))

    conversation = client.get(f"/conversations/{posted.json()['conversation_id']}").json()
    step = conversation["turns"][0]["steps"][1]
    assert step["ok"] is True
    result = step["result"]
    assert result["source"] == "public status page"
    assert result["observed_at"] == NOW.isoformat()
    assert datetime.fromisoformat(result["page_updated_at"]) < NOW
    assert {row["component"] for row in result["components"]} == {"web_app", "api", "login", "payments"}
    assert "asia" in {row["region"] for row in result["components"]}
    assert {row["status"] for row in result["components"]} == {"operational"}
    assert result["open_incidents"] == []
