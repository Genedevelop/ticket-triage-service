import pytest

from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT


# run one turn in which the model searches, and return the recorded tool step
def search(client, llm, query):
    llm.script(call("kb_search", query=query), answer(DRAFT))
    posted = client.post("/tickets", json=request_body("t3", NOW))
    conversation = client.get(f"/conversations/{posted.json()['conversation_id']}").json()
    return conversation["turns"][0]["steps"][1]


def test_the_dark_mode_article_says_the_app_follows_the_operating_system(client, llm):
    step = search(client, llm, "dark mode")

    assert step["ok"] is True
    assert step["result"]["source"] == "knowledge base"
    article = step["result"]["articles"][0]
    assert set(article) == {"id", "title", "excerpt", "updated_at", "score"}
    assert article["id"] == "kb_dark_mode"
    assert "Settings > Appearance" in article["excerpt"]
    assert "System Default" in article["excerpt"]
    assert "follows the operating system" in article["excerpt"]
    assert article["score"] > 0


def test_keywords_are_joined_with_or_so_one_matching_word_is_enough(client, llm):
    step = search(client, llm, "dark zebra unicorn")

    assert [article["id"] for article in step["result"]["articles"]] == ["kb_dark_mode"]


def test_punctuation_in_a_query_is_dropped(client, llm):
    step = search(client, llm, "dark-mode!!! & (toggle) | 'x")

    assert step["ok"] is True
    assert step["result"]["articles"][0]["id"] == "kb_dark_mode"


def test_at_most_three_articles_are_returned_best_first(client, llm):
    step = search(client, llm, "dark payment status export refund pro")

    scores = [article["score"] for article in step["result"]["articles"]]
    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)


def test_a_query_matching_nothing_is_an_empty_result_not_an_error(client, llm):
    step = search(client, llm, "zebra")

    assert step["ok"] is True
    assert step["result"]["articles"] == []


@pytest.mark.parametrize("query", ["", "?!... --", "โหมดมืด", "the of and"])
def test_a_query_with_no_usable_token_is_a_tool_error(client, llm, query):
    step = search(client, llm, query)

    assert step["ok"] is False
    assert step["result"]["error"]["type"] == "invalid_query"
    assert step["result"]["error"]["retryable"] is True
