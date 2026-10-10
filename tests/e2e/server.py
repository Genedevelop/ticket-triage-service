# the application the end-to-end tests serve: the real composition root with the scripted model, the mock providers and a fixed clock
import json
import os
from datetime import datetime
from typing import Any

from backend.domain.ports import Providers
from backend.infrastructure.llm.scripted import ScriptedLLM
from backend.infrastructure.providers.sample_data import load_seed
from main import create_app
from tests.conftest import NOW

# the scripted model's steps as a JSON list, in the shape call() and answer() build
MODEL_SCRIPT_KEY = "E2E_MODEL_SCRIPT"
# the fault modes queued on the billing mock's coming refund calls, as a JSON list
BILLING_FAULTS_KEY = "E2E_BILLING_FAULTS"


# the factory uvicorn is given: everything but the model, the clock and the queued faults is what the served application runs with
def create_scripted_app() -> Any:
    llm = ScriptedLLM()
    llm.script(*json.loads(os.environ.get(MODEL_SCRIPT_KEY, "[]")))
    billing_faults = json.loads(os.environ.get(BILLING_FAULTS_KEY, "[]"))

    def load_sample_data(providers: Providers, now: datetime) -> None:
        load_seed(providers, now)
        providers.billing.faults.extend(billing_faults)

    return create_app(llm=llm, clock=lambda: NOW, load_sample_data=load_sample_data)
