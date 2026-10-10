# one module per resource, gathered here into one router; part of backend.api and imports what it may
from fastapi import APIRouter

from backend.api.routes import actions, conversations, health, tickets

router = APIRouter()
for resource in (health, tickets, conversations, actions):
    router.include_router(resource.router)
