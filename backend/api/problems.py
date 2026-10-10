from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.core.errors import (
    AppError,
    CallFailed,
    Conflict,
    Denied,
    Internal,
    Invalid,
    Misconfigured,
    NotFound,
    Unavailable,
)

MEDIA_TYPE = "application/problem+json"
TYPE_PREFIX = "urn:ticket-triage:problem:"
# the type of a problem that says no more than its status code
BLANK = "about:blank"
# seconds a caller is asked to wait after the model or a provider could not be reached
RETRY_AFTER_S = 5
INTERNAL_STATUS = 500
VALIDATION_STATUS = 422

# the status each category of error is answered with
STATUS: dict[type[AppError], int] = {
    NotFound: 404,
    Conflict: 409,
    Invalid: 422,
    Denied: 403,
    Unavailable: 503,
    Misconfigured: 503,
    CallFailed: 502,
    Internal: INTERNAL_STATUS,
}


def status_of(error: AppError) -> int:
    return next((status for category, status in STATUS.items() if isinstance(error, category)), INTERNAL_STATUS)


def _response(
    status: int, problem_type: str, title: str, detail: str, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {"type": problem_type, "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, headers=headers, media_type=MEDIA_TYPE)


def problem_for(error: AppError) -> JSONResponse:
    # Step 1: Ask the caller to come back only after a failure that may pass
    headers = {"Retry-After": str(RETRY_AFTER_S)} if isinstance(error, Unavailable) else None
    # Step 2: Answer with the error's code, title, message and details under its category's status
    return _response(status_of(error), TYPE_PREFIX + error.code, error.title, error.message, headers, **error.details)


async def _app_error(_: Request, error: AppError) -> JSONResponse:
    return problem_for(error)


async def _framework_http_error(_: Request, error: HTTPException) -> JSONResponse:
    return _response(error.status_code, BLANK, HTTPStatus(error.status_code).phrase, str(error.detail), error.headers)


async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    # Step 1: Locate each invalid part: whether it is in the body, path, query or header, and where inside it
    errors = [
        {
            "location": str(found["loc"][0]),
            "pointer": "".join(f"/{part}" for part in found["loc"][1:]),
            "detail": found["msg"],
        }
        for found in error.errors()
    ]
    # Step 2: Answer with every invalid part in one problem document
    detail = f"{len(errors)} part(s) of the request are missing or invalid"
    return _response(VALIDATION_STATUS, TYPE_PREFIX + "validation-error", "Request validation failed", detail, errors=errors)


def install(app: FastAPI) -> None:
    # Step 1: Answer every category with a problem document, leaving an internal failure to the server's plain 500
    for category, status in STATUS.items():
        if status != INTERNAL_STATUS:
            app.add_exception_handler(category, _app_error)
    # Step 2: Answer the framework's own errors, an unknown path or a refused method or body, in the same shape
    app.add_exception_handler(HTTPException, _framework_http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
