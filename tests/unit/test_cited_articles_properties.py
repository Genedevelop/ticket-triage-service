from collections import Counter

from hypothesis import event, given, settings
from hypothesis import strategies as st

from backend.application.tools.registry import citable_tool_names, read_tool_names
from backend.domain import decision
from tests.unit.strategies import STEPS, TOOL_NAMES, TURNS, TURNS_CITING_RETURNED_AND_UNRETURNED_ARTICLES, drafts_citing

# the registered tools with citable articles, or any other set of names given that mark
CITABLE_TOOLS = st.just(citable_tool_names()) | st.frozensets(TOOL_NAMES, max_size=3)


# twice the examples, because a kept article needs a cited id, a step that returned it and a tool that may be cited to meet
@settings(max_examples=200)
@given(TURNS, CITABLE_TOOLS)
def test_the_articles_kept_are_the_cited_ones_a_successful_citable_tool_step_returned_in_the_order_cited(turn, citable_tools):
    draft, steps = turn
    returned = {
        article["id"]
        for step in steps
        if step["kind"] == "tool" and step["ok"] and step["name"] in citable_tools
        for article in step["result"].get("articles", [])
    }

    kept = decision.from_draft(
        draft, steps, [], "scripted-model", "triage_v2@test", read_tools=read_tool_names(), citable_tools=citable_tools
    )["kb_article_ids"]

    event("an article is kept" if kept else "no article is kept")
    assert kept == [article_id for article_id in draft.kb_article_ids if article_id in returned]
    assert set(kept) <= returned and set(kept) <= set(draft.kb_article_ids)


def _ids_returned_by_a_successful_step_of(tools, steps):
    return {
        article["id"]
        for step in steps
        if step["kind"] == "tool" and step["ok"] and step["name"] in tools
        for article in step["result"].get("articles", [])
    }


@given(TURNS_CITING_RETURNED_AND_UNRETURNED_ARTICLES)
def test_a_draft_citing_returned_and_unreturned_articles_keeps_each_returned_one_as_often_and_in_the_order_it_was_cited(turn):
    draft, steps, never_returned = turn
    citable_tools = citable_tool_names()
    returned = _ids_returned_by_a_successful_step_of(citable_tools, steps)
    cited = draft.kb_article_ids

    kept = decision.from_draft(
        draft, steps, [], "scripted-model", "triage_v2@test", read_tools=read_tool_names(), citable_tools=citable_tools
    )["kb_article_ids"]

    assert kept, "the strategy must make a cited article and a returned one meet"
    assert set(kept) <= returned
    assert not set(kept) & set(never_returned)
    assert len(kept) < len(cited)
    assert Counter(kept) == Counter(article_id for article_id in cited if article_id in returned)
    assert max(Counter(kept).values()) >= 2
    positions = iter(range(len(cited)))
    assert all(any(cited[position] == article_id for position in positions) for article_id in kept)
    assert kept == [article_id for article_id in cited if article_id in returned]


@given(TURNS)
def test_no_article_is_kept_when_no_tool_has_citable_articles(turn):
    draft, steps = turn
    kept = decision.from_draft(
        draft, steps, [], "scripted-model", "triage_v2@test", read_tools=read_tool_names(), citable_tools=frozenset()
    )["kb_article_ids"]

    assert kept == []


# turns in which no step is a tool call, though each is named like one and may carry articles
TURNS_WITHOUT_A_TOOL_CALL = STEPS.map(lambda steps: [{**step, "kind": "model"} for step in steps]).flatmap(
    lambda steps: st.tuples(drafts_citing(steps), st.just(steps))
)


@given(TURNS_WITHOUT_A_TOOL_CALL)
def test_no_article_is_kept_from_a_step_that_is_not_a_tool_call_whatever_its_name_and_result(turn):
    draft, steps = turn
    named_like_the_steps = frozenset(step["name"] for step in steps)

    kept = decision.from_draft(
        draft, steps, [], "scripted-model", "triage_v2@test", read_tools=read_tool_names(), citable_tools=named_like_the_steps
    )["kb_article_ids"]

    assert kept == []
