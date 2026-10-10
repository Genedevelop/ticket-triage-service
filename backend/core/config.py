import os
from dataclasses import dataclass

from backend.core.errors import Misconfigured

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_LOG_LEVEL = "INFO"


class SettingMissing(Misconfigured):
    code = "setting-missing"
    title = "Setting missing"


@dataclass(frozen=True)
class Settings:
    database_url: str
    # empty means no key was provided: the service still starts, and model calls report "misconfigured"
    llm_api_key: str
    model: str
    # sent only when set, because a non-reasoning model rejects the parameter
    reasoning_effort: str | None
    log_level: str


def load_settings(environment: dict[str, str] | None = None) -> Settings:
    # Step 1: Read the process environment unless the caller passed its own
    environment = os.environ if environment is None else environment
    # Step 2: Refuse to start without a database URL, the only setting that has no default
    database_url = environment.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SettingMissing("DATABASE_URL is not set")
    # Step 3: Fill every other setting, taking its default when the key is unset or blank
    return Settings(
        database_url=database_url,
        llm_api_key=environment.get("OPENAI_API_KEY", "").strip(),
        model=environment.get("TRIAGE_MODEL", "").strip() or DEFAULT_MODEL,
        reasoning_effort=environment.get("TRIAGE_REASONING_EFFORT", "").strip() or None,
        log_level=environment.get("TRIAGE_LOG_LEVEL", "").strip().upper() or DEFAULT_LOG_LEVEL,
    )
