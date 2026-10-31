"""Health and readiness.

The `database` field reflects an actual query. A health check that always returns
`ok` is worse than no health check, because it teaches you to ignore it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db

logger = logging.getLogger("markaz.health")

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    db_state = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health: database probe failed: %s", exc)
        db_state = "unreachable"

    return {
        "status": "ok" if db_state == "ok" else "degraded",
        "version": "1.0.0",
        "database": db_state,
        "environment": settings.environment,
    }
