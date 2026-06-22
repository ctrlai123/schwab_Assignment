from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..circuit_breaker import account_service_breaker
from ..database import get_db
from ..metrics import snapshot
from ..services import account_client

router = APIRouter()


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as exc:
        db_status = f"unhealthy: {exc}"

    account_health = await account_client.check_health()

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "event-gateway",
        "database": db_status,
        "account_service": account_health,
        "circuit_breaker": account_service_breaker.get_status(),
        "metrics": snapshot(),
    }
