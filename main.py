from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from backend.api.app import build
from backend.application import triage_turn
from backend.application.actions.execute import Timing, recover_unfinished
from backend.application.model_input import Prompt, load_prompt
from backend.core import logging as log
from backend.core.config import Settings, load_settings
from backend.domain.ports import LLM, Providers
from backend.infrastructure.llm.openai_responses import OpenAIResponsesLLM
from backend.infrastructure.postgres import db, kb
from backend.infrastructure.providers import mock_providers
from backend.infrastructure.providers.sample_data import load_seed

Clock = Callable[[], datetime]
# fills the providers with what the service is shown with; the one used by default needs the mocks
SampleDataLoader = Callable[[Providers, datetime], None]


def system_clock() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    llm: LLM | None = None,
    providers: Providers | None = None,
    clock: Clock | None = None,
    settings: Settings | None = None,
    load_sample_data: SampleDataLoader | None = None,
    prompt: Prompt | None = None,
) -> Any:
    # Step 1: Load the settings and set up the one logger
    settings = settings or load_settings()
    log.configure(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: Any):
        # Step 1: Open the pool and bring the schema and the knowledge base up to date
        app.state.pool = db.open_pool(settings.database_url)
        try:
            db.apply_schema(app.state.pool)
            kb.seed_articles(app.state.pool)
            # Step 2: Wire the providers, the mocks unless the caller passed its own
            app.state.providers = providers or mock_providers(app.state.pool)
            # Step 3: Load the sample data into the mocks built here, or into the caller's providers with its own loader
            loader = load_sample_data or (load_seed if providers is None else None)
            if loader is not None:
                loader(app.state.providers, app.state.clock())
            # Step 4: Finish what a previous process left running: dead turns first, then the effects a person approved
            triage_turn.clean_up_at_start_up(app.state.pool, app.state.prompt)
            recover_unfinished(app.state.pool, app.state.providers, app.state.action_timing)
            yield
        finally:
            # Step 5: Close the pool at shut-down
            app.state.pool.close()

    # Step 2: Build the HTTP application around that start-up and shut-down
    app = build(lifespan)
    # Step 3: Wire the model, the clock, the prompt and the executor's timing onto the application state
    app.state.llm = llm or OpenAIResponsesLLM(settings.llm_api_key, settings.model, settings.reasoning_effort)
    app.state.clock = clock or system_clock
    app.state.prompt = prompt or load_prompt()
    app.state.action_timing = Timing()
    return app
