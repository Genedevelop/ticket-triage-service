import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.application.model_input import PROMPT_FILE, Prompt, load_prompt
from backend.core.config import load_settings
from backend.domain.ports import LLM, LLMMisconfigured
from backend.infrastructure.llm.openai_responses import OpenAIResponsesLLM
from backend.infrastructure.postgres import db
from backend.infrastructure.providers import mock_providers
from backend.infrastructure.providers.sample_data import THREADS
from evals import scenarios, score
from evals.cases import ADVERSARIAL_CASES, BY_ID, CASES, OPERATOR_CASES, Case
from main import create_app

EVAL_DATABASE = "triage_eval"
DEFAULT_RUNS = 3
# the moment every case's last message arrives
NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
KEPT_TABLES = ("kb_articles",)
MS_PER_S = 1000
FIRST_ERROR_STATUS = 400
MISCONFIGURED_STATUS = 503
MISCONFIGURED_TYPE_SUFFIX = "model-misconfigured"
# exit codes: a case failed its labels, or the model could not be used at all
EXIT_FAILED_RUNS = 1
EXIT_MODEL_UNUSABLE = 2


def refuse_served(database_url: str) -> None:
    served = conninfo_to_dict(os.environ["DATABASE_URL"]).get("dbname")
    if conninfo_to_dict(database_url).get("dbname") == served:
        raise RuntimeError("the evaluation must not run on the served database")


def recreate_database(served_url: str) -> str:
    # Step 1: Name the evaluation's own database on the served server, and refuse the served database itself
    database_url = make_conninfo(served_url, dbname=EVAL_DATABASE)
    refuse_served(database_url)
    # Step 2: Drop and recreate it, so every evaluation starts empty
    with psycopg.connect(served_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{EVAL_DATABASE}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{EVAL_DATABASE}"')
    return database_url


def prompt_stems() -> list[str]:
    return sorted(path.stem for path in PROMPT_FILE.parent.glob("*.md"))


def request_body(case: Case, now: datetime) -> dict[str, Any]:
    # Step 1: Take the case's prefix of the thread, whose last message arrives now
    thread = THREADS[case.thread_name]
    messages = thread["messages"][: case.messages_arrived]
    arrived = messages[-1][0]
    body = {
        "customer": thread["customer"],
        "messages": [
            {"author": "customer", "body": text, "sent_at": (now - timedelta(minutes=minutes - arrived)).isoformat()}
            for minutes, text in messages
        ],
    }
    # Step 2: Add the injected text to the end of the last message when the case has one
    if case.injected is not None:
        body["messages"][-1]["body"] += "\n\n" + case.injected
    return body


def _empty_tables(pool: Any) -> None:
    with pool.connection() as conn:
        # Step 1: List every table except the knowledge base, which is seeded once at start-up
        tables = [
            row[0]
            for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            if row[0] not in KEPT_TABLES
        ]
        # Step 2: Empty them all in one statement
        conn.execute("TRUNCATE " + ", ".join(f'"{table}"' for table in tables) + " RESTART IDENTITY CASCADE")


def _post_case(client: TestClient, case: Case) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    # Step 1: Post the case's ticket, stopping the whole evaluation when the model is misconfigured
    posted = client.post("/tickets", json=request_body(case, NOW))
    if posted.status_code == MISCONFIGURED_STATUS and posted.json()["type"].endswith(MISCONFIGURED_TYPE_SUFFIX):
        raise LLMMisconfigured(posted.json()["detail"])
    if posted.status_code >= FIRST_ERROR_STATUS:
        raise RuntimeError(f"{case.id}: POST /tickets answered {posted.status_code}: {posted.text}")
    conversation_id = posted.json()["conversation_id"]
    decision, previous = posted.json()["decision"], None
    # Step 2: Send the operator's message when the case has one; the decision of that turn is the one scored
    if case.operator_message is not None:
        said = client.post(f"/conversations/{conversation_id}/messages", json={"body": case.operator_message})
        if said.status_code >= FIRST_ERROR_STATUS:
            raise RuntimeError(f"{case.id}: the operator turn answered {said.status_code}: {said.text}")
        decision, previous = said.json()["decision"], decision
    return conversation_id, decision, previous


def run_case(case: Case, llm: LLM, database_url: str, prompt: Prompt | None = None) -> dict[str, Any]:
    # Step 1: Build a fresh application on the evaluation database with a fixed clock, and the given prompt if any
    settings = load_settings({**os.environ, "DATABASE_URL": database_url})
    pool = db.open_pool(database_url)
    try:
        providers = mock_providers(pool)
        app = create_app(llm=llm, providers=providers, clock=lambda: NOW, settings=settings, prompt=prompt)
        with TestClient(app) as client:
            # Step 2: Empty the tables and load the provider state behind the case
            _empty_tables(pool)
            scenarios.load(providers, case.thread_name, case.messages_arrived, NOW, case.tool_down)
            # Step 3: Run the case's turns and time them
            started = time.monotonic()
            conversation_id, decision, previous = _post_case(client, case)
            latency_ms = int((time.monotonic() - started) * MS_PER_S)
            # Step 4: Read the conversation back, for the tokens its model steps used
            conversation = client.get(f"/conversations/{conversation_id}").json()
    finally:
        pool.close()
    # Step 5: Score the last decision against the case's labels, beside its latency and tokens
    model_steps = [step for turn in conversation["turns"] for step in turn["steps"] if step["kind"] == "model"]
    scored_steps = conversation["turns"][-1]["steps"]
    return {
        **score.score_run(case, decision, previous),
        "decision": decision,
        "conversation": conversation,
        "effects_proposed": score.effects_proposed(scored_steps),
        "first_call_input_tokens": score.first_call_input_tokens(scored_steps),
        "latency_ms": latency_ms,
        "input_tokens": sum(step["input_tokens"] or 0 for step in model_steps),
        "output_tokens": sum(step["output_tokens"] or 0 for step in model_steps),
    }


def run(cases: list[Case], runs: int, llm: LLM, database_url: str, prompt: Prompt | None = None) -> list[dict[str, Any]]:
    refuse_served(database_url)
    return [score.summarize(case, [run_case(case, llm, database_url, prompt) for _ in range(runs)]) for case in cases]


def main(argv: list[str] | None = None) -> int:
    # Step 1: Read which cases to run, how often, and with which committed prompt file
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Run the labelled cases with the real model.")
    parser.add_argument("--case", action="append", choices=list(BY_ID), help="a case id; may be given more than once")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"runs per case (default {DEFAULT_RUNS})")
    parser.add_argument(
        "--prompt", choices=prompt_stems(), default=PROMPT_FILE.stem,
        help=f"the stem of a prompt file under backend/application/prompts (default {PROMPT_FILE.stem}, the shipped one)",
    )
    options = parser.parse_args(argv)
    if options.runs < 1:
        parser.error("--runs must be at least 1")
    cases = [BY_ID[case_id] for case_id in options.case] if options.case else [*CASES, *OPERATOR_CASES, *ADVERSARIAL_CASES]
    # Step 2: Run them with the real model on the evaluation's own database, noting when the evaluation started
    prompt = load_prompt(PROMPT_FILE.with_name(f"{options.prompt}.md"))
    settings = load_settings()
    llm = OpenAIResponsesLLM(settings.llm_api_key, settings.model, settings.reasoning_effort)
    started = datetime.now(timezone.utc)
    try:
        summaries = run(cases, options.runs, llm, recreate_database(settings.database_url), prompt)
    except LLMMisconfigured as error:
        print(f"the model cannot be used: {error}", file=sys.stderr)
        return EXIT_MODEL_UNUSABLE
    # Step 3: Print what was evaluated, then the scores, and exit non-zero when any run failed its labels
    print(score.format_header(summaries, settings.model, options.runs, started))
    print(score.format_table(summaries))
    return EXIT_FAILED_RUNS if any(summary["failed_runs"] for summary in summaries) else 0


if __name__ == "__main__":
    sys.exit(main())
