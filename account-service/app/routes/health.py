from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as exc:
        db_status = f"unhealthy: {exc}"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "account-service",
        "database": db_status,
    }
