# the one error model: what an error carries, and the status and the problem document it is answered with
import pytest

from backend.api import problems
from backend.application.tools.tool import ToolError
from backend.core import errors
from backend.core.config import SettingMissing, load_settings
from backend.core.errors import AppError
from backend.domain.ports import ProviderError

PROBLEM = "application/problem+json"


def test_a_missing_database_url_is_a_misconfiguration():
    with pytest.raises(SettingMissing) as refused:
        load_settings({})

    assert isinstance(refused.value, errors.Misconfigured)
    assert refused.value.message == "DATABASE_URL is not set"


@pytest.mark.parametrize("key", ["type", "title", "status", "detail"])
def test_details_that_would_replace_a_member_of_the_problem_document_are_refused(key):
    with pytest.raises(errors.ReservedDetail):
        errors.Conflict("taken", details={key: "x"})


@pytest.mark.parametrize(
    "error, status",
    [
        (errors.NotFound("action"), 404),
        (errors.Conflict("taken"), 409),
        (errors.Invalid("bad"), 422),
        (errors.Denied("no"), 403),
        (errors.Unavailable("later"), 503),
        (errors.Misconfigured("fix it"), 503),
        (errors.CallFailed("refused"), 502),
        (errors.Internal("bug"), 500),
        (errors.Busy("c1"), 409),
        (AppError("no category"), 500),
    ],
)
def test_an_error_is_answered_with_the_status_of_its_category(error, status):
    assert problems.status_of(error) == status


def test_an_error_carries_its_code_message_and_details():
    error = errors.Conflict("already taken", details={"key": "k1"})

    assert (error.code, error.message, error.details, str(error)) == ("conflict", "already taken", {"key": "k1"}, "already taken")
    assert errors.NotFound("action").details == {}


def test_a_typed_failure_names_its_details_as_its_attributes_and_a_tool_result_keeps_the_keys_the_model_reads():
    refused = ProviderError("rate_limited", "slow down", is_retryable=True)
    error = ToolError.from_provider(refused)

    assert refused.details == error.details == {"error_type": "rate_limited", "is_retryable": True}
    assert (error.error_type, error.is_retryable) == (error.details["error_type"], error.details["is_retryable"])
    assert error.as_result() == {"error": {"type": "rate_limited", "message": "slow down", "retryable": True}}


def test_an_app_error_becomes_a_problem_built_from_its_own_fields():
    error = errors.Busy("c1")

    response = problems.problem_for(error)

    assert (response.status_code, response.media_type) == (409, PROBLEM)
    assert response.body == (
        b'{"type":"urn:ticket-triage:problem:conversation-busy","title":"Conversation busy","status":409,'
        b'"detail":"another turn is running on this conversation; send the request again when it has finished",'
        b'"conversation_id":"c1"}'
    )
    assert "retry-after" not in response.headers


def test_only_an_unavailable_error_asks_the_caller_to_retry():
    assert problems.problem_for(errors.Unavailable("later")).headers["retry-after"] == str(problems.RETRY_AFTER_S)
    assert "retry-after" not in problems.problem_for(errors.Misconfigured("fix it")).headers
