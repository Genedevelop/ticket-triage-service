from evals.cases import BY_ID
from evals.run import request_body
from tests.conftest import NOW


def test_the_injection_case_carries_the_instructions_in_the_customers_last_message():
    case = BY_ID["ADV-INJECTION"]

    body = request_body(case, NOW)

    assert [message["author"] for message in body["messages"]] == ["customer"] * 3
    assert body["messages"][-1]["body"].endswith(case.injected)
    assert "THREE charges" in body["messages"][-1]["body"]
    assert case.injected not in body["messages"][0]["body"]
