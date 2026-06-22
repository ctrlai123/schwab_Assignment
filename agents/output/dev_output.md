# Development Audit Report
## Event Gateway / Account Service Microservices

---

## 1. Error Handling Audit

### ✅ Paths That Handle Errors Well

| Location | What's Done Right |
|---|---|
| `account_client.apply_transaction` | Differentiates `TimeoutException`, `ConnectError`, `HTTPStatusError`; 4xx errors skip the circuit breaker and do not retry; exhausted retries raise `503` with context |
| `account_client.get_balance` | Catches network errors and `HTTPStatusError` separately; raises typed `HTTPException` with meaningful messages |
| `account_client.check_health` | Bare `except Exception` is *appropriate here* — health probes must never crash; returns structured degraded payload |
| `events.create_event` | Catches `HTTPException` from the account client, marks event `failed` in DB before re-raising, and increments error metrics |
| `events.get_event` | Returns `404` with a human-readable `detail` string |
| `health.health_check` (both services) | DB probe wrapped in `try/except`; failure degrades gracefully without crashing the endpoint |
| `main.py` (event-gateway) | `422` exception handler provides structured JSON; `tracing_middleware` does not swallow errors |

### ❌ Paths Missing Error Handling

#### `events.create_event` — DB commit before account call is unprotected

```python
# Current — no try/except around the first commit
db.add(db_event)
db.commit()          # ← SQLAlchemyError here leaves no audit trail and returns 500

# Also: after HTTPException is re-raised the DB session is left in
# an indeterminate state if refresh() was called on a failed row.
```

**Risk:** A DB constraint violation (duplicate PK race) returns an unhandled `500` with a raw SQLAlchemy traceback exposed to the caller.

#### `account_client.get_balance` — no retry logic

```python
# apply_transaction retries 3× with back-off.
# get_balance makes exactly one attempt and fails immediately.
# These two functions have inconsistent resilience contracts.
```

#### `accounts.apply_transaction` — DB commit unprotected

```python
db.add(tx)
db.commit()    # ← no try/except; SQLAlchemyError → raw 500
db.refresh(tx)
```

**Risk:** Concurrent duplicate submissions that slip past the idempotency check (race window) will produce an unhandled integrity error.

#### `accounts._get_or_create_account` — silent race condition

```python
def _get_or_create_account(db, account_id):
    account = db.query(Account)...first()
    if not account:
        account = Account(account_id=account_id)
        db.add(account)
        db.commit()   # ← IntegrityError on concurrent creation; no handling
```

#### `account-service` — no global `500` exception handler

The account-service `main.py` registers no `exception_handler` for unhandled exceptions. Raw Python tracebacks can leak through Uvicorn's default handler, exposing internal details.

#### `health.health_check` (account-service) — no logging

```python
@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    # No logger — silent on both healthy and unhealthy outcomes
```

#### `events.list_events` — no upper bound on result set

```python
events = q.order_by(Event.event_timestamp.asc()).all()
# No LIMIT — full table scan on large datasets returns 200 with potential MB payload
```

---

## 2. Logging & Auditing Audit

### ✅ Good Coverage

| Path | Coverage |
|---|---|
| Both `main.py` middleware | Entry + exit logged with method, path, status, latency, and `trace_id` |
| `create_event` | Entry, duplicate detection, success, and failure all logged |
| `apply_transaction` (account-service) | Entry, idempotent hit, and success logged |
| `get_balance` / `get_account` | Entry logged with `trace_id` |
| `account_client` | Every retry attempt, circuit breaker state transitions, and final success logged |
| `circuit_breaker.py` | `OPEN`, `HALF_OPEN`, and `CLOSED` transitions logged |

### ❌ Silent Code Paths

| Location | Gap |
|---|---|
| `health.health_check` (account-service) | Zero log output — unhealthy DB goes completely silent |
| `health.health_check` (event-gateway) | DB unhealthy path logs nothing |
| `events.get_event` — 404 branch | No `logger.warning` when event is not found |
| `events.list_events` — empty result | No log when zero events are returned (ambiguous — misconfiguration vs. genuinely empty) |
| `accounts._get_or_create_account` | Account auto-creation is a financial side-effect with no audit log |
| `accounts.get_account` — 404 branch | `raise HTTPException` with no preceding log |
| `accounts.get_balance` — 404 branch | Same: raises without logging |
| `account_client.get_balance` — circuit open | No `logger.warning` before raising `503` (contrast: `apply_transaction` logs this) |
| `main.py` (account-service) | `trace_id` is set in middleware but **never stored on `request.state`**, so route handlers cannot access it via `request.state.trace_id` |

### ❌ Missing Financial Audit Trail

This is the most critical gap. Financial systems require immutable, structured audit events distinct from operational logs.

```
MISSING: Structured audit log for every monetary state change:
  - Account auto-creation (implicit in _get_or_create_account)
  - Transaction applied (amount, type, account, resulting balance)
  - Event status transitions: pending → processed / failed
  - Circuit breaker blocking a transaction (money not moved)
```

There is no `audit_logger` emitting to a separate sink (e.g., a dedicated log stream, audit table, or message queue). The current `logger.info("Transaction applied")` is insufficient for compliance — it mixes operational noise with financial facts.

---

## 3. Observability Gaps

### Metrics

| Gap | Impact |
|---|---|
| In-memory `defaultdict` counters reset on restart | Metrics are lost on every deploy; useless for SLO tracking |
| No Prometheus / OpenMetrics exposition (`/metrics` endpoint) | Cannot be scraped by Prometheus, Grafana, Datadog, etc. |
| No histogram for request latency | Cannot calculate P50/P95/P99 — latency SLOs are unmeasurable |
| No circuit breaker state metric | Cannot alert on `OPEN` state without parsing logs |
| No `event.status` distribution counter | Cannot observe `pending`/`processed`/`failed` ratio over time |
| No per-currency or per-account-type breakdown | Financial reporting impossible from metrics alone |
| `account-service` has zero metrics instrumentation | The service processing all money movement is completely dark |

### Distributed Tracing

| Gap | Impact |
|---|---|
| `trace_id` is a plain string, not an OpenTelemetry `TraceContext` | Cannot correlate spans in Jaeger/Zipkin/Tempo; no parent-child span relationships |
| No span created around `account_client.apply_transaction` | Cannot measure account-service call latency separately from gateway latency |
| No span around DB queries | Slow queries are invisible in traces |
| `trace_id` not stored on `request.state` in account-service | Downstream logs cannot be correlated back to the originating gateway request |

### Alerting

| Missing Alert | Why It Matters |
|---|---|
| Circuit breaker transitions to `OPEN` | Money is being rejected; on-call must know immediately |
| `event.status = failed` rate > threshold | Systematic processing failures need a page |
| `POST /events` error rate > X% | SLO breach |
| DB health degraded | Both services go dark on DB failure |
| Account-service returning 5xx to gateway | Distinct from gateway-side errors |
| P99 latency on `POST /events` > threshold | Latency SLO breach |

### Structural Observability Gap

```
The /health endpoint exposes circuit breaker state and in-memory metrics —
this is a good start, but it is not machine-readable in a standard format.
A Kubernetes liveness/readiness probe treats any 2xx as healthy regardless
of {"status": "degraded"} in the body. The health endpoint should return
HTTP 503 when status is "degraded" to integrate correctly with orchestrators.
```

---

## 4. Git Commit Message Suggestions

```
feat(event-gateway): add idempotent event ingestion with circuit-breaker-protected account client

Implements POST /events with idempotency check, pending→processed/failed
status lifecycle, and an httpx-based account client featuring retry with
exponential back-off and a thread-safe circuit breaker (CLOSED/OPEN/HALF_OPEN).
```

```
feat(account-service): implement transaction application and balance calculation endpoints

Adds POST /accounts/{id}/transactions with idempotency guard,
GET /accounts/{id}/balance with running CREDIT/DEBIT aggregation,
and GET /accounts/{id} with last-20 transaction history.
Auto-creates account on first transaction.
```

```
chore(infra): add docker-compose with internal account-service network and healthcheck gating

Exposes only event-gateway on port 8000; account-service is internal-only.
event-gateway startup is gated on account-service healthcheck to prevent
connection errors during rolling deploys.
```

```
feat(observability): add structured JSON logging, trace-id propagation, and in-memory metrics

Both services emit JSON logs with timestamp, level, service name, and
trace_id. HTTP middleware assigns/forwards x-trace-id across the service
boundary. Custom request/error counters exposed via /health.
```

```
fix(resilience): prevent raw 500s on DB errors in event and transaction creation routes

Wrap SQLAlchemy commits in try/except, add global 500 handler to
account-service, and guard _get_or_create_account against concurrent
IntegrityError on account auto-creation.
```

---

## 5. Top 3 Improvement Suggestions

---

### Improvement 1 — Protect All DB Commits and Add a Global Exception Handler

**Problem:** Unhandled `SQLAlchemyError` produces a raw `500` that may leak table/column names and leaves the session in a broken state.

**event-gateway — `routes/events.py`:**
```python
from sqlalchemy.exc import SQLAlchemyError

@router.post("/events", response_model=EventResponse, status_code=201)
async def create_event(
    request: Request,
    event: EventCreate,
    db: Session = Depends(get_db),
):
    trace_id = request.state.trace_id
    extra = {"trace_id": trace_id}
    track("POST /events")
    logger.info("Processing event %s for account %s",
                event.eventId, event.accountId, extra=extra)

    existing = db.query(Event).filter(Event.event_id == event.eventId).first()
    if existing:
        logger.info("Duplicate event %s – returning stored record",
                    event.eventId, extra=extra)
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(_to_response(existing)),
        )

    db_event = Event(
        event_id=event.eventId,
        account_id=event.accountId,
        type=event.type,
        amount=event.amount,
        currency=event.currency,
        event_timestamp=_naive(event.eventTimestamp),
        metadata_=event.metadata,
        received_at=_naive(datetime.now(timezone.utc)),
        status="pending",
    )
    db.add(db_event)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to persist event %s: %s",
                     event.eventId, exc, extra=extra, exc_info=True)
        track("POST /events", error=True)
        raise HTTPException(status_code=500, detail="Failed to store event")

    try:
        await account_client.apply_transaction(
            account_id=event.accountId,
            event_id=event.eventId,
            event_type=event.type,
            amount=event.amount,
            currency=event.currency,
            event_timestamp=event.eventTimestamp.isoformat(),
            trace_id=trace_id,
        )
        db_event.status = "processed"
    except HTTPException:
        db_event.status = "failed"
        track("POST /events", error=True)
        logger.error("Event %s failed account-service call",
                     event.eventId, extra=extra)
        raise
    finally:
        # Always attempt to persist the terminal status
        try:
            db.commit()
            db.refresh(db_event)
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("Failed to update event status for %s: %s",
                         event.eventId, exc, extra=extra, exc_info=True)

    logger.info("Event %s processed successfully", event.eventId, extra=extra)
    return _to_response(db_event)
```

**account-service — `main.py` (add global handler):**
```python
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        "Unhandled database error on %s", request.url.path,
        extra={"trace_id": trace_id},
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "A database error occurred"},
    )

@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        "Unhandled exception on %s", request.url.path,
        extra={"trace_id": trace_id},
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred"},
    )
```

---

### Improvement 2 — Add a Dedicated Audit Logger for Financial Events

**Problem:** Financial state changes (account creation, transaction application, event status transitions) are mixed into operational logs with no way to route, query, or retain them separately.

**New file — shared `audit.py` (one copy per service, or a shared lib):**
```python
# event-gateway/app/audit.py  (mirror in account-service/app/audit.py)
import json
import logging
from datetime import datetime, timezone


class AuditLogger:
    """
    Emits structured financial audit events to a dedicated logger.
    In production, configure this logger's handler to write to an
    immutable sink (e.g., append-only S3, Kafka topic, audit DB table).
    """

    def __init__(self, service: str):
        self._logger = logging.getLogger(f"{service}.audit")

    def _emit(self, event_type: str, payload: dict, trace_id: str | None):
        entry = {
            "audit_timestamp": datetime.now(timezone.utc).isoformat(),
            "audit_event": event_type,
            "trace_id": trace_id,
            **payload,
        }
        self._logger.info(json.dumps(entry), extra={"trace_id": trace_id})

    def event_received(self, event_id: str, account_id: str,
                       amount: float, currency: str,
                       event_type: str, trace_id: str | None):
        self._emit("EVENT_RECEIVED", {
            "event_id": event_id,
            "account_id": account_id,
            "amount": amount,
            "currency": currency,
            "event_type": event_type,