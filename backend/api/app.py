from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

from backend.api import problems
from backend.api.routes import router
from backend.core import logging as log

REQUEST_ID_HEADER = "X-Request-Id"


def build(lifespan: Any) -> FastAPI:
    # Step 1: Create the application around the given start-up and shut-down, with the request-id middleware
    app = FastAPI(title="Support Ticket Triage Service", lifespan=lifespan)

    @app.middleware("http")
    async def carry_request_id(request: Request, call_next: Any) -> Any:
        # Step 1: Take the caller's id when it sent one, so its logs and ours meet
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        # Step 2: Handle the request with the id bound to every log line
        with log.bound(request_id=request_id):
            response = await call_next(request)
        # Step 3: Return the id to the caller
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    # Step 2: Mount the routes and the handlers that answer every error as a problem document
    app.include_router(router)
    problems.install(app)
    return app
