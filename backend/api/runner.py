from typing import Any

from backend.application import triage_turn


def build_runner(state: Any) -> triage_turn.Runner:
    return triage_turn.Runner(state.pool, state.llm, state.providers, state.prompt, state.action_timing)
