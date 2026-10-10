from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import ANSWERED, CONFLICT


def post(client, llm, body, *steps):
    llm.script(*steps)
    return client.post("/tickets", json=body).json()["decision"]


def test_a_decision_keeps_the_unattended_answer_when_the_article_it_cites_was_returned(client, llm):
    decision = post(client, llm, request_body("t3", NOW), call("kb_search", query="dark mode"), answer(ANSWERED))

    assert decision["next_action"] == "auto_respond"
    assert decision["kb_article_ids"] == ["kb_dark_mode"]
    assert decision["overrides"] == []


def test_a_decision_citing_an_article_no_search_returned_is_routed_and_says_why(client, llm):
    decision = post(client, llm, request_body("t3", NOW), answer(ANSWERED))

    assert decision["next_action"] == "route_to_specialist"
    assert decision["kb_article_ids"] == []
    assert [(override["rule"], override["from"], override["to"]) for override in decision["overrides"]] == [
        ("R3", "auto_respond", "route_to_specialist")
    ]


def test_a_decision_after_a_failed_lookup_is_routed_and_both_rules_are_recorded(client, llm):
    body = request_body("t3", NOW)
    del body["customer"]
    draft = {**ANSWERED, "evidence_conflicts": [CONFLICT]}

    decision = post(client, llm, body, call("billing_lookup"), call("kb_search", query="dark mode"), answer(draft))

    assert decision["next_action"] == "route_to_specialist"
    assert [override["rule"] for override in decision["overrides"]] == ["R3", "R4"]
