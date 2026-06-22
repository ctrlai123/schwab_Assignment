import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .database import Base, engine
from .logging_config import setup_logging
from .routes import events, health

logger = setup_logging("event-gateway")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Event Gateway API", version="1.0.0")


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    # Single authoritative trace_id for the entire request lifetime.
    # Route handlers must read from request.state.trace_id — never re-derive.
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.time()

    logger.info(
        "Incoming request: %s %s", request.method, request.url.path,
        extra={"trace_id": trace_id},
    )

    response = await call_next(request)
    response.headers["x-trace-id"] = trace_id

    ms = (time.time() - start) * 1000
    logger.info(
        "Request done: %s %s → %s (%.1fms)",
        request.method, request.url.path, response.status_code, ms,
        extra={"trace_id": trace_id},
    )
    return response


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.warning("Validation error on %s", request.url.path, extra={"trace_id": trace_id})
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors() if hasattr(exc, "errors") else str(exc)},
    )


app.include_router(events.router)
app.include_router(health.router)


@app.get("/")
async def root():
    return {
        "service": "Event Gateway API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "POST /events": "Submit a transaction event",
            "GET /events/{id}": "Get event by ID",
            "GET /events?account={accountId}": "List events for an account",
            "GET /health": "Health check",
        },
    }
