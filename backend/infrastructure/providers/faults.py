import time

from backend.core.errors import Internal
from backend.domain.ports import ProviderError


class SimulatedCrash(Internal):
    code = "simulated-crash"
    title = "Simulated crash"


class UnknownFault(Internal):
    code = "unknown-fault"
    title = "Unknown fault"


# what a mock can be told to do on its next acting call
FAULTS = ("slow", "fail_before", "lose_answer", "crash_before", "crash_after", "wrong_charge", "wrong_amount")
# how many hex characters of a random id a mock puts into the ids it issues
ISSUED_ID_HEX_CHARS = 16


def fault_before_acting(faults: list[str], timeout_s: float) -> str | None:
    # Step 1: Take the fault queued for this call, refusing a mode no mock knows
    fault = faults.pop(0) if faults else None
    if fault is not None and fault not in FAULTS:
        raise UnknownFault(f"unknown fault mode: {fault}")
    # Step 2: Apply the faults that stop the call before the provider acts
    if fault == "slow":
        time.sleep(timeout_s)
        raise ProviderError("timeout", "the provider did not answer in time", is_retryable=True)
    if fault == "fail_before":
        raise ProviderError("unavailable", "the provider is unavailable", is_retryable=True)
    if fault == "crash_before":
        raise SimulatedCrash("the process died before the provider acted")
    # Step 3: Hand any other fault back, to be applied once the provider has acted
    return fault


def fault_after_acting(fault: str | None) -> None:
    if fault == "lose_answer":
        raise ProviderError("timeout", "the provider acted and its answer was lost", is_retryable=True)
    if fault == "crash_after":
        raise SimulatedCrash("the process died after the provider acted")
