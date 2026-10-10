from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.api.runner import build_runner
from backend.application import conversations, triage_turn
from backend.core.errors import NotFound

router = APIRouter()


class OperatorMessageIn(BaseModel):
    body: str = Field(min_length=1)


@router.post(
    "/conversations/{conversation_id}/messages",
    description="Store the operator's message once per key and run a turn on it; the reply comes with the new decision version.",
)
def operator_message(
    conversation_id: UUID,
    message: OperatorMessageIn,
    request: Request,
    idempotency_key: Annotated[str | None, Header(max_length=255)] = None,
) -> JSONResponse:
    # Step 1: Store the message and run a turn on it, reporting a conversation that does not exist
    state = request.app.state
    outcome = triage_turn.answer_operator(build_runner(state), state.clock(), conversation_id, message.body, idempotency_key or None)
    if outcome is None:
        raise NotFound("conversation")
    # Step 2: Answer with the reply to the operator beside the new decision version
    decision = outcome.decision
    body = {"conversation_id": str(conversation_id), "reply": decision["operator_reply"], "decision": decision}
    return JSONResponse(body)


@router.get(
    "/conversations/{conversation_id}",
    description="Messages, every decision version, actions with their events, and turns with their steps.",
)
def read_conversation(conversation_id: UUID, request: Request) -> JSONResponse:
    # Step 1: Read everything stored about the conversation
    conversation = conversations.read(request.app.state.pool, conversation_id)
    # Step 2: Report a conversation that does not exist, or answer with it as it is now
    if conversation is None:
        raise NotFound("conversation")
    return JSONResponse(jsonable_encoder(conversation))
