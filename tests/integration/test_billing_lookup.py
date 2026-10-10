import json

from backend.domain.ports import ProviderError
from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT


# the tool's result as the model was given it: the last input item of its next call
def result_given_to_the_model(llm):
    last_item = llm.requests[1]["items"][-1]
    assert last_item["type"] == "function_call_output"
    return json.loads(last_item["output"])


def lookup_of(client, llm, body):
    llm.script(call("billing_lookup"), answer(DRAFT))
    posted = client.post("/tickets", json=body)
    assert posted.status_code == 201
    (turn,) = client.get(f"/conversations/{posted.json()['conversation_id']}").json()["turns"]
    (step,) = [step for step in turn["steps"] if step["name"] == "billing_lookup"]
    return step, posted.json()["decision"]


def test_a_customer_the_billing_system_does_not_know_is_told_to_the_model_as_a_tool_error(client, llm):
    step, decision = lookup_of(client, llm, {**request_body("t1", NOW), "customer": {"id": "cust_never_billed"}})

    assert step["ok"] is False
    assert step["result"] == {
        "error": {"type": "account_not_found", "message": "the billing system has no account for this customer", "retryable": False}
    }
    assert [(tool["name"], tool["ok"]) for tool in decision["tools_called"]] == [("billing_lookup", False)]
    assert result_given_to_the_model(llm) == step["result"]


def test_a_billing_system_that_does_not_answer_is_told_to_the_model_as_an_error_worth_another_try(client, llm, providers, monkeypatch):
    def unanswered(customer_id, now):
        raise ProviderError("timeout", "the billing system did not answer", is_retryable=True)

    monkeypatch.setattr(providers.billing, "lookup", unanswered, raising=False)

    step, decision = lookup_of(client, llm, request_body("t1", NOW))

    assert step["result"] == {"error": {"type": "timeout", "message": "the billing system did not answer", "retryable": True}}
    assert [(tool["name"], tool["ok"]) for tool in decision["tools_called"]] == [("billing_lookup", False)]
    assert result_given_to_the_model(llm) == step["result"]
