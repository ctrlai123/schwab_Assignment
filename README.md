# Event Ledger

Two-microservice financial transaction system built with **Python 3.11 / FastAPI / SQLite**.

> **AI-assisted engineering:** Built with [Claude Code](https://claude.ai/code) acting as Design, Development, and QA agent throughout the SDLC. See [AI_USAGE.md](AI_USAGE.md) for details.

| Document | Description |
|---|---|
| [DESIGN.md](DESIGN.md) | Design document — architecture, sequence diagrams, data models, decisions |
| [AI_USAGE.md](AI_USAGE.md) | How Claude Code was used as Design / Dev / QA agent |
| [docs/COVERAGE_REPORT.md](docs/COVERAGE_REPORT.md) | Unit test coverage summary (95% / 80%) |
| [docs/FUNCTIONAL_TEST_REPORT.md](docs/FUNCTIONAL_TEST_REPORT.md) | Live functional test results (15/15 pass) |
| [docs/coverage/account-service/](docs/coverage/account-service/) | HTML coverage report — Account Service |
| [docs/coverage/event-gateway/](docs/coverage/event-gateway/) | HTML coverage report — Event Gateway |

---

## Architecture

```
Browser / Client
       │
       ▼  REST (public)
┌─────────────────────────┐
│     Event Gateway       │  :8000
│  • validates input      │
│  • enforces idempotency │──── REST (internal) ────►  ┌──────────────────┐
│  • circuit breaker      │                            │  Account Service  │  :8001
│  • own SQLite DB        │◄───────────────────────────│  • balances       │
└─────────────────────────┘                            │  • own SQLite DB  │
                                                       └──────────────────┘
```

- **Event Gateway** – public entry point. Receives events, validates them, enforces idempotency against its own SQLite DB, then calls Account Service via REST.
- **Account Service** – internal only. Manages account balances and transaction history in its own separate SQLite DB.
- The two services share **no database and no in-process state**.
- Communication is **synchronous REST** (HTTP).

### Trace propagation

The Gateway generates a UUID `trace_id` for every incoming request (or re-uses the client's `X-Trace-Id` header). The single authoritative `trace_id` is stored in `request.state` by the middleware so every log line, every downstream call, and the response header all carry the **same** ID — no divergence within a request.

The trace ID is forwarded to the Account Service as the `X-Trace-Id` header and is embedded in every structured JSON log line produced by both services, making a single client request traceable end-to-end.

**On OpenTelemetry:** The spec lists OTel as *preferred but not required*. This solution implements manual `X-Trace-Id` propagation, which satisfies all minimum tracing requirements (generation, propagation, structured log embedding, end-to-end traceability). OTel was not added to keep the dependency footprint minimal and avoid the OTel Collector / Jaeger infrastructure needed to observe OTel traces in development. Adding OTel SDK instrumentation (`opentelemetry-instrumentation-fastapi`) is a direct drop-in extension — the trace ID contract between the services would remain identical.

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Python | 3.11 | Only needed for manual / test runs |
| pip | any | Bundled with Python |
| Docker Desktop | any recent | Required for Docker Compose path |
| Docker Compose | v2 (`docker compose`) | Bundled with Docker Desktop |

---

## Setup — Docker Compose (recommended)

```bash
cd event-ledger
docker compose up --build
```

Both images are built and started automatically. `event-gateway` waits for `account-service` to pass its health check before starting.

- **Gateway (public):** http://localhost:8000  ← only entry point for clients
- Account Service is internal — no host port exposed; reachable only by the Gateway inside Docker's network.

To stop and remove containers:

```bash
docker compose down
```

---

## Setup — Manual (without Docker)

**Terminal 1 – start Account Service first**

```bash
cd event-ledger/account-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

**Terminal 2 – start Event Gateway**

```bash
cd event-ledger/event-gateway
pip install -r requirements.txt
ACCOUNT_SERVICE_URL=http://localhost:8001 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> On Windows use `set ACCOUNT_SERVICE_URL=http://localhost:8001` before the uvicorn command.

---

## Running the tests

Each service has its own isolated test suite using an in-memory SQLite database. No running services are required.

**Account Service (10 tests)**

```bash
cd event-ledger/account-service
pip install -r requirements.txt
pytest tests/
```

**Event Gateway (23 tests)**

```bash
cd event-ledger/event-gateway
pip install -r requirements.txt
pytest tests/
```

Test coverage includes:
- Core functionality: idempotency, out-of-order ordering, balance calculation, input validation
- Resiliency: 503 on Account Service failure, circuit breaker state transitions, GET endpoints still work when Account Service is down
- Trace propagation: trace ID flows from Gateway → Account Service
- Integration: full Gateway → Account Service request flow

---

## API reference

### Event Gateway — port 8000

| Method | Path | Description |
|---|---|---|
| `POST` | `/events` | Submit a transaction event |
| `GET` | `/events/{id}` | Retrieve a single event by ID |
| `GET` | `/events?account={accountId}` | List events for an account (chronological) |
| `GET` | `/accounts/{accountId}/balance` | Proxy to Account Service balance (returns 503 if unreachable) |
| `GET` | `/health` | Health check + circuit breaker state + metrics |

### Account Service — internal only (not accessible to clients)

The Account Service has no exposed port. It is reachable only by the Event Gateway
over Docker's internal network (`http://account-service:8001`). Clients must use
the Gateway for all interactions.

| Method | Path | Called by |
|---|---|---|
| `POST` | `/accounts/{accountId}/transactions` | Gateway (on every new event) |
| `GET` | `/accounts/{accountId}/balance` | Gateway balance proxy |
| `GET` | `/accounts/{accountId}` | Gateway (internal use) |
| `GET` | `/health` | Docker health check only |

### Example — submit an event

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "evt-001",
    "accountId": "acct-123",
    "type": "CREDIT",
    "amount": 150.00,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11Z",
    "metadata": {"source": "mainframe-batch", "batchId": "B-9042"}
  }'
```

Response codes:
- `201 Created` — new event accepted and processed
- `200 OK` — duplicate `eventId`; original event returned, balance unchanged
- `422 Unprocessable Entity` — validation failure (missing field, bad type, zero/negative amount)
- `503 Service Unavailable` — Account Service unreachable

---

## Resiliency pattern: Circuit Breaker + Timeout + Retry with exponential back-off

The Gateway implements all three layers on every call to the Account Service:

| Layer | Configuration | Purpose |
|---|---|---|
| **Timeout** | 5 s per attempt | Prevents the Gateway from hanging on a slow Account Service |
| **Retry with exponential back-off** | 3 attempts, delays 0.5 s → 1 s → 2 s | Handles transient network blips without hammering a struggling service |
| **Circuit Breaker** | Opens after 5 failures; recovers after 30 s | Fast-fails calls when the Account Service is repeatedly down, protecting both the Gateway's thread pool and the Account Service from excess load during recovery |

**Why all three?** They address different failure modes. Timeout stops an individual slow call. Retry handles brief flaps. The circuit breaker handles sustained outages — once open, the Gateway returns `503` immediately without waiting for timeouts or retries, allowing the Account Service time to recover.

**Circuit Breaker states:**
```
CLOSED (normal) ──(5 failures)──► OPEN (fast-fail 503)
                                      │
                              (30 s elapsed)
                                      ▼
                                 HALF_OPEN (one probe call)
                                 success ──► CLOSED
                                 failure ──► OPEN
```

---

## Observability

- **Structured logging** — every log line from both services is a JSON object containing `timestamp`, `level`, `service`, `message`, and `trace_id`. Example:
  ```json
  {"timestamp": "2026-05-15T14:02:11Z", "level": "INFO", "service": "event-gateway", "message": "Event evt-001 processed successfully", "trace_id": "a1b2c3d4-..."}
  ```
- **Health endpoints** — `GET /health` on both services reports database connectivity. The Gateway additionally reports circuit breaker state and request/error metrics.
- **Custom metrics** — `request_counts` and `error_counts` per endpoint are exposed on the Gateway's `/health` response.

---

## Constraints

| Constraint | Decision |
|---|---|
| **Language** | Python 3.11 |
| **Database** | SQLite (embedded, no separate server). Each service owns its own `.db` file; files are ephemeral inside Docker containers and reset on `docker compose down`. |
| **Communication** | Synchronous REST via `httpx`. The Gateway `await`s the Account Service response before returning — request/response, not fire-and-forget. |
| **Tracing** | Manual `X-Trace-Id` UUID propagation (OpenTelemetry preferred but not required — see [Trace propagation](#trace-propagation) above for rationale). |
| **Docker** | `docker-compose.yml` provided; Account Service has no host-port mapping (internal only). |
| **Framework** | FastAPI |

---

## Graceful degradation

| Scenario | Behaviour |
|---|---|
| Account Service down — `POST /events` | Returns `503 Service Unavailable` immediately (no hang, no 500) |
| Account Service down — `GET /events/{id}` | Returns event from Gateway's local DB — **unaffected** |
| Account Service down — `GET /events?account=…` | Returns events from Gateway's local DB — **unaffected** |
| Account Service down — `GET /accounts/{id}/balance` (via Gateway) | Returns `503` with clear message: *"Account Service is unreachable"* |
