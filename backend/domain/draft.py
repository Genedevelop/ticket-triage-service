from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.domain.ports import strict_schema

Urgency = Literal["critical", "high", "medium", "low"]
ProductArea = Literal["billing", "account", "availability", "app", "integrations", "other"]
IssueType = Literal["outage", "bug", "billing_dispute", "how_to", "feature_request", "account_change", "other"]
Sentiment = Literal["negative", "neutral", "positive", "mixed"]
NextAction = Literal["auto_respond", "route_to_specialist", "escalate_to_human"]
Team = Literal["billing", "technical", "account"]


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    claim: str
    contradicted_by: str


# field order is the order the model writes in: what it observed first, its verdict after
class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    sentiment: Sentiment
    product_area: ProductArea
    issue_type: IssueType
    additional_issue_types: list[IssueType]
    evidence_conflicts: list[EvidenceConflict]
    rationale: str
    urgency: Urgency
    next_action: NextAction
    route_to: Team | None
    kb_article_ids: list[str]
    customer_reply_draft: str | None
    operator_reply: str | None


DRAFT_SCHEMA = strict_schema(Draft)


def parse_draft(text: str | None) -> Draft:
    return Draft.model_validate_json(text or "")
