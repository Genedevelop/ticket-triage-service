from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.application.tools.tool import Tool, ToolContext, ToolError
from backend.infrastructure.postgres import kb


class KbSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def _kb_search(arguments: KbSearchArguments, context: ToolContext) -> dict[str, Any]:
    # Step 1: Search, asking the model for English keywords when the query holds nothing searchable
    try:
        articles = kb.search(context.pool, arguments.query)
    except kb.NoUsableToken as error:
        raise ToolError(
            "invalid_query", "the query holds no searchable English keyword; search again with English keywords", is_retryable=True
        ) from error
    # Step 2: Report the articles with their source and the time they were read
    return {
        "source": "knowledge base",
        "observed_at": context.now.isoformat(),
        "articles": [{**article, "updated_at": article["updated_at"].isoformat()} for article in articles],
    }


KB_SEARCH = Tool(
    name="kb_search",
    description=(
        "Search the knowledge base of help articles. The query is a few English keywords, whatever the ticket's "
        "language; an article matching any of them can be returned. Returns up to 3 articles, best match first, each "
        "with its id, title, an excerpt, when it was last updated and a score."
    ),
    arguments=KbSearchArguments,
    handler=_kb_search,
    has_citable_articles=True,
)
