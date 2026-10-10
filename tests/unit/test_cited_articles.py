from backend.application.tools.registry import REGISTRY, citable_tool_names, read_tool_names
from backend.application.tools.tool import NoArguments, Tool
from backend.domain import decision
from backend.domain.draft import Draft
from tests.unit.test_rules import DRAFT

CITING = Draft.model_validate({**DRAFT, "kb_article_ids": ["kb_dark_mode", "faq_billing"]})


def step(name, article_id, is_ok=True):
    return {"kind": "tool", "name": name, "arguments": {}, "result": {"articles": [{"id": article_id}]}, "ok": is_ok, "duration_ms": 1}


def cited(steps):
    return decision.from_draft(
        CITING, steps, [], "scripted-model", "triage_v2@test", read_tools=read_tool_names(), citable_tools=citable_tool_names()
    )["kb_article_ids"]


def test_only_the_search_tool_returns_articles_a_decision_may_cite():
    assert [name for name, tool in REGISTRY.items() if tool.has_citable_articles] == ["kb_search"]


def test_a_decision_keeps_only_the_articles_a_tool_with_citable_articles_returned():
    assert cited([step("kb_search", "kb_dark_mode")]) == ["kb_dark_mode"]
    assert cited([step("kb_search", "kb_dark_mode", is_ok=False)]) == []
    assert cited([step("status_check", "faq_billing")]) == []


def test_a_second_tool_with_citable_articles_needs_no_change_to_the_decision(monkeypatch):
    faq_search = Tool(name="faq_search", description="", arguments=NoArguments, handler=lambda arguments, context: {}, has_citable_articles=True)
    monkeypatch.setitem(REGISTRY, faq_search.name, faq_search)

    assert cited([step("kb_search", "kb_dark_mode"), step("faq_search", "faq_billing")]) == ["kb_dark_mode", "faq_billing"]
