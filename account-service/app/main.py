import time
import uuid
from fastapi import FastAPI, Request
from .database import Base, engine
from .routes import accounts, health
from .logging_config import setup_logging

logger = setup_logging("account-service")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Account Service", version="1.0.0")


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    request.state.trace_id = trace_id  # make trace_id available to route handlers
    start = time.time()
    logger.info(
        "Incoming request: %s %s", request.method, request.url.path,
        extra={"trace_id": trace_id},
    )
    response = await call_next(request)
    ms = (time.time() - start) * 1000
    logger.info(
        "Request done: %s %s → %s (%.1fms)",
        request.method, request.url.path, response.status_code, ms,
        extra={"trace_id": trace_id},
    )
    return response


app.include_router(accounts.router)
app.include_router(health.router)


@app.get("/")
async def root():
    return {
        "service": "Account Service",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "POST /accounts/{accountId}/transactions": "Apply a transaction",
            "GET /accounts/{accountId}/balance": "Get account balance",
            "GET /accounts/{accountId}": "Get account details",
            "GET /health": "Health check",
        },
    }
