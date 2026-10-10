import pytest

from backend.core.errors import Internal
from backend.domain.ports import ProviderError
from backend.infrastructure.providers.faults import FAULTS, SimulatedCrash, UnknownFault, fault_after_acting, fault_before_acting


def test_a_fault_mode_no_mock_knows_is_refused_by_name_and_taken_off_the_queue():
    queued = ["explode", "slow"]

    with pytest.raises(UnknownFault) as refused:
        fault_before_acting(queued, timeout_s=0)

    assert isinstance(refused.value, Internal)
    assert refused.value.message == "unknown fault mode: explode"
    assert queued == ["slow"]


def test_a_call_with_no_fault_queued_goes_through_untouched():
    assert fault_before_acting([], timeout_s=0) is None
    assert fault_after_acting(None) is None


def test_a_provider_that_fails_before_acting_reports_a_failure_worth_another_attempt():
    with pytest.raises(ProviderError) as unavailable:
        fault_before_acting(["fail_before"], timeout_s=0)
    with pytest.raises(ProviderError) as timed_out:
        fault_before_acting(["slow"], timeout_s=0)

    assert (unavailable.value.error_type, unavailable.value.is_retryable) == ("unavailable", True)
    assert (timed_out.value.error_type, timed_out.value.is_retryable) == ("timeout", True)


@pytest.mark.parametrize("fault", ["lose_answer", "crash_after", "wrong_charge", "wrong_amount"])
def test_a_fault_that_applies_after_the_provider_acted_is_handed_back_and_only_one_call_is_affected(fault):
    queued = [fault]

    assert fault in FAULTS
    assert fault_before_acting(queued, timeout_s=0) == fault
    assert queued == []


def test_a_lost_answer_is_a_time_out_worth_another_attempt_and_a_crash_is_not_a_provider_error():
    with pytest.raises(ProviderError) as lost:
        fault_after_acting("lose_answer")
    with pytest.raises(SimulatedCrash):
        fault_after_acting("crash_after")
    with pytest.raises(SimulatedCrash):
        fault_before_acting(["crash_before"], timeout_s=0)

    assert (lost.value.error_type, lost.value.is_retryable) == ("timeout", True)
    assert lost.value.message == "the provider acted and its answer was lost"
    assert not issubclass(SimulatedCrash, ProviderError)
