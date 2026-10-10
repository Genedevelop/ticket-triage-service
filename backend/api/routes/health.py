from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.application import health

router = APIRouter()


@router.get("/healthz", description="Report that the process is up and whether the database is reachable.")
def healthz(request: Request) -> JSONResponse:
    is_database_reachable = health.is_database_reachable(request.app.state.pool)
    body = {"status": "ok" if is_database_reachable else "unavailable", "database": is_database_reachable}
    return JSONResponse(body, status_code=200 if is_database_reachable else 503)
