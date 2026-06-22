# Event Ledger – Comprehensive Design Document

## Executive Summary

The Event Ledger system is a dual-service, event-driven financial transaction platform composed of a public-facing **Event Gateway** and an internal **Account Service**. The Gateway accepts, deduplicates, and persists transaction events before forwarding them to the Account Service for balance computation, with full idempotency guarantees at both service boundaries. Resiliency is enforced via a thread-safe Circuit Breaker, exponential-backoff retry, and per-request timeouts, ensuring graceful degradation when the Account Service is unavailable.

---

## System Architecture Overview

```mermaid
graph TB
    subgraph External["External Zone (Public)"]
        Client["API Client / Consumer"]
    end

    subgraph Gateway["Event Gateway  :8000"]
        direction TB
        MW["Tracing Middleware\n(x-trace-id)"]
        ER["Events Router\n/events"]
        HR["Health Router\n/health"]
        CB["Circuit Breaker\naccount_service_breaker"]
        AC["Account Client\n(httpx + retry)"]
        GDB[("Event Gateway DB\nSQLite / Postgres\nEvents Table")]
        MET["Metrics Tracker\ntrack()"]
    end

    subgraph AccountSvc["Account Service  :8001 (internal only)"]
        direction TB
        AR["Accounts Router\n/accounts"]
        ADB[("Account Service DB\nSQLite / Postgres\nAccounts + Transactions")]
    end

    subgraph Infra["Infrastructure (Docker Compose)"]
        DN["Docker Network\nbridge"]
    end

    Client -->|"HTTP :8000"| MW
    MW --> ER
    MW --> HR
    ER --> GDB
    ER --> CB
    CB --> AC
    AC -->|"HTTP :8001 (internal)"| DN
    DN --> AR
    AR --> ADB
    ER --> MET

    style External fill:#e8f4f8,stroke:#2196F3
    style Gateway fill:#fff8e1,stroke:#FF9800
    style AccountSvc fill:#f3e5f5,stroke:#9C27B0
    style Infra fill:#e8f5e9,stroke:#4CAF50
```

---

## Sequence Diagram – `POST /events` Main Flow

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant MW as Tracing Middleware
    participant GW as Events Router
    participant GDB as Gateway DB
    participant CB as Circuit Breaker
    participant AC as Account Client
    participant AS as Account Service
    participant ADB as Account Service DB

    Client->>+MW: POST /events {eventId, accountId, type, amount, ...}
    Note over MW: Assign / propagate x-trace-id
    MW->>+GW: Forward request + request.state.trace_id

    %% ── Idempotency Check ──
    GW->>+GDB: SELECT * FROM events WHERE event_id = ?
    GDB-->>-GW: result

    alt Event already exists
        GW-->>Client: 200 OK – stored EventResponse (idempotent replay)
    else New event
        %% ── Persist pending ──
        GW->>+GDB: INSERT event (status = "pending")
        GDB-->>-GW: committed

        %% ── Circuit Breaker gate ──
        GW->>+CB: can_proceed()?

        alt Circuit OPEN
            CB-->>GW: false
            GW->>GDB: UPDATE status = "failed"
            GW-->>Client: 503 Service Unavailable
        else Circuit CLOSED or HALF_OPEN probe
            CB-->>-GW: true

            %% ── Call Account Service (with retry) ──
            GW->>+AC: apply_transaction(accountId, eventId, ...)
            AC->>+AS: POST /accounts/{accountId}/transactions\n[x-trace-id header]

            AS->>+ADB: SELECT transaction WHERE event_id = ?
            ADB-->>-AS: existing?

            alt Transaction already applied (idempotent)
                AS-->>AC: 200 alreadyApplied=true
            else New transaction
                AS->>+ADB: INSERT transaction
                ADB-->>-AS: committed
                AS-->>AC: 201 TransactionResponse
            end

            AC-->>-GW: success response
            CB->>CB: record_success()

            GW->>GDB: UPDATE status = "processed"
            GW-->>-Client: 201 Created – EventResponse (status=processed)
        end

        alt Account Service error / timeout
            AC-->>GW: HTTPException (502/503/504)
            CB->>CB: record_failure()
            GW->>GDB: UPDATE status = "failed"
            GW-->>Client: 4xx / 5xx
        end
    end

    MW-->>-Client: Attach x-trace-id response header
```

---

## Data Model

```mermaid
erDiagram
    %% ── Event Gateway DB ─────────────────────────────────────────
    EVENTS {
        string  event_id          PK  "Client-supplied UUID"
        string  account_id            "Target account reference"
        string  type                  "CREDIT | DEBIT"
        float   amount                "Transaction amount > 0"
        string  currency              "ISO-4217 (e.g. USD)"
        datetime event_timestamp      "Business time (naive UTC)"
        json    metadata_             "Arbitrary key-value bag"
        datetime received_at          "Gateway arrival time (naive UTC)"
        string  status                "pending | processed | failed"
    }

    %% ── Account Service DB ───────────────────────────────────────
    ACCOUNTS {
        string   account_id      PK  "Auto-created on first transaction"
        datetime created_at          "Row creation timestamp"
    }

    TRANSACTIONS {
        string   event_id        PK  "FK = Events.event_id (idempotency key)"
        string   account_id      FK  "→ ACCOUNTS.account_id"
        string   type                "CREDIT | DEBIT"
        float    amount              "Transaction amount"
        string   currency            "ISO-4217"
        datetime event_timestamp     "Original business time"
        datetime applied_at          "When Account Service wrote the row"
    }

    ACCOUNTS ||--o{ TRANSACTIONS : "has"
```

> **Note:** `EVENTS` lives in the Gateway database; `ACCOUNTS` and `TRANSACTIONS` live in the Account Service database. The two databases are logically separate (one per container).

---

## API Contract

### Event Gateway (`http://localhost:8000`)

| Method | Path | Request Body / Params | Success Response | Error Responses | Description |
|--------|------|-----------------------|-----------------|-----------------|-------------|
| `POST` | `/events` | `EventCreate` JSON body | `201 EventResponse`<br>`200 EventResponse` (duplicate) | `422` validation<br>`503` circuit open<br>`502/504` upstream | Submit a new transaction event; idempotent on `eventId` |
| `GET` | `/events/{event_id}` | Path: `event_id` (string) | `200 EventResponse` | `404` not found | Retrieve a single event by its ID |
| `GET` | `/events` | Query: `account` (optional string) | `200 EventListResponse` | — | List all events, optionally filtered by `accountId`, ordered by `event_timestamp ASC` |
| `GET` | `/accounts/{account_id}/balance` | Path: `account_id` | `200 BalanceResponse` | `404` account not found<br>`503` upstream unavailable | Proxy balance query to Account Service; degrades gracefully |
| `GET` | `/health` | — | `200 {"status":"ok"}` | — | Gateway liveness / readiness probe |
| `GET` | `/` | — | `200` service manifest JSON | — | Service discovery root |

### Account Service (`http://account-service:8001` – internal only)

| Method | Path | Request Body / Headers | Success Response | Error Responses | Description |
|--------|------|------------------------|-----------------|-----------------|-------------|
| `POST` | `/accounts/{accountId}/transactions` | `TransactionRequest` JSON body<br>`x-trace-id` header | `201 TransactionResponse`<br>`200` (already applied, `alreadyApplied=true`) | `422` validation | Apply a CREDIT or DEBIT; idempotent on `eventId` |
| `GET` | `/accounts/{accountId}/balance` | Path: `accountId`<br>`x-trace-id` header | `200 BalanceResponse` (`balance`, `currency`, `transactionCount`) | `404` not found | Compute running balance by summing all transactions |
| `GET` | `/accounts/{accountId}` | Path: `accountId`<br>`x-trace-id` header | `200 AccountDetailsResponse` (balance + last 20 transactions) | `404` not found | Full account details with recent transaction history |
| `GET` | `/health` | — | `200 {"status":"ok"}` | — | Account Service liveness probe |

### Schema Reference

```
EventCreate          → { eventId, accountId, type, amount, currency, eventTimestamp, metadata? }
EventResponse        → { eventId, accountId, type, amount, currency, eventTimestamp, metadata,
                         receivedAt, status }
EventListResponse    → { events: EventResponse[], total: int }
TransactionRequest   → { eventId, type, amount, currency, eventTimestamp }
TransactionResponse  → { eventId, accountId, type, amount, currency, eventTimestamp,
                         appliedAt, alreadyApplied }
BalanceResponse      → { accountId, balance, currency, transactionCount }
AccountDetailsResponse → { accountId, balance, currency, recentTransactions, createdAt }
```

---

## Resiliency Patterns

### Circuit Breaker State Diagram

```mermaid
stateDiagram-v2
    [*] --> CLOSED

    CLOSED --> CLOSED : call succeeds\nrecord_success() → failure_count=0

    CLOSED --> OPEN : failure_count ≥ threshold (5)\nrecord_failure() → state=OPEN\nrecord last_failure_time

    OPEN --> OPEN : can_proceed()=false\nelapsed < recovery_timeout (30s)\nreject immediately → 503

    OPEN --> HALF_OPEN : can_proceed() called\nelapsed ≥ recovery_timeout\nallow 1 probe call

    HALF_OPEN --> CLOSED : probe succeeds\nrecord_success()\nfailure_count reset

    HALF_OPEN --> OPEN : probe fails\nrecord_failure()\nback to OPEN immediately

    note right of CLOSED
        Normal operation.
        All calls pass through.
    end note

    note right of OPEN
        Fast-fail mode.
        No calls to Account Service.
        Returns 503 to client.
    end note

    note right of HALF_OPEN
        Recovery probe.
        Exactly 1 call allowed
        (half_open_max_calls=1).
    end note
```

### Retry Policy (Account Client)

```mermaid
stateDiagram-v2
    [*] --> ATTEMPT

    ATTEMPT --> SUCCESS : HTTP 2xx received
    ATTEMPT --> TRANSIENT_FAIL : 429 / 502 / 503 / 504\nor connection error

    TRANSIENT_FAIL --> WAIT : attempt ≤ max_retries (3)
    TRANSIENT_FAIL --> PERMANENT_FAIL : attempt > max_retries

    WAIT --> ATTEMPT : sleep(backoff)\nbackoff = base × 2^(attempt-1)\n+ jitter (capped at 30s)

    SUCCESS --> [*] : return response
    PERMANENT_FAIL --> [*] : raise HTTPException\n(propagates to Circuit Breaker)

    note right of WAIT
        Attempt 1 → ~1s
        Attempt 2 → ~2s
        Attempt 3 → ~4s
        Per-attempt timeout: 10s
    end note
```

### Timeout Hierarchy

| Layer | Mechanism | Value | Action on Breach |
|-------|-----------|-------|-----------------|
| Per HTTP call to Account Service | `httpx` request timeout | 10 s | Raises `TimeoutException` → counted as failure |
| Total retry budget | Retry policy ceiling | ~30 s | Raises `HTTPException(504)` |
| Docker health-check | `urllib.request` probe | 5 s timeout | Container marked unhealthy; restarts |

---

## Key Design Decisions

| # | Decision | Choice Made | Rationale |
|---|----------|-------------|-----------|
| 1 | **Service decomposition** | Two services: Event Gateway + Account Service | Separation of concerns – the Gateway owns event persistence/idempotency; the Account Service owns financial state. Enables independent scaling and deployment. |
| 2 | **Idempotency strategy** | Client-supplied `eventId` as the idempotency key, checked at both service boundaries | Prevents duplicate balance mutations from network retries. Double-checked in both Gateway (`events` table) and Account Service (`transactions` table) for defense-in-depth. |
| 3 | **Event status state machine** | `pending → processed \| failed` written to Gateway DB before calling downstream | Provides an audit trail and allows dead-letter recovery. The Gateway DB record exists even if the Account Service is down. |
| 4 | **Circuit Breaker implementation** | Custom thread-safe `CircuitBreaker` class (no external lib) | Zero additional dependencies; full control over thresholds, recovery timeout, and logging. Singleton `account_service_breaker` shared across all requests. |
| 5 | **Account Service network isolation** | No host ports exposed; reachable only via Docker internal network | Reduces attack surface; the Account Service is never directly callable from outside the compose stack, enforcing the Gateway as the single ingress. |
| 6 | **Trace propagation** | Single `x-trace-id` generated/forwarded by Tracing Middleware; propagated as HTTP header to Account Service | End-to-end request correlation across service boundaries without a full distributed tracing framework (e.g., Jaeger). |
| 7 | **Timezone handling** | Strip timezone info (`_naive()`) before DB writes | SQLite (and some Postgres ORM configurations) do not store `tzinfo`; naive UTC is stored consistently and avoids comparison bugs. |
| 8 | **Balance computation** | Real-time sum of all transactions at query time | Simple, always-consistent. Trade-off accepted: O(n) per balance query. A running-balance column would be needed at scale. |
| 9 | **Auto-create accounts** | Account row created on first transaction if it doesn't exist | Eliminates a separate account-registration step, simplifying the client workflow at the cost of losing a formal account-creation audit event. |
| 10 | **Metrics** | Lightweight `track()` counter function | Provides basic observability without requiring Prometheus/StatsD. Designed as a seam to be replaced with a real metrics backend. |
| 11 | **Database per service** | Each service owns its own DB (shared-nothing) | Prevents tight coupling at the data layer. Schema changes in one service do not affect the other. |
| 12 | **FastAPI + SQLAlchemy ORM** | Synchronous SQLAlchemy sessions via `Depends(get_db)` in otherwise async FastAPI handlers | Pragmatic choice for SQLite compatibility and simplicity. Full async (`asyncpg`) would be preferred for production Postgres. |

---

## Constraints and Trade-offs

### Constraints

| Constraint | Impact |
|------------|--------|
| **SQLite as default DB** | Not safe for multi-process/multi-replica deployments; single-file database with writer contention. Must be replaced with Postgres for any horizontal scaling. |
| **Synchronous DB sessions in async handlers** | SQLAlchemy sync sessions block the event loop under high concurrency. Acceptable for low-to-medium load; requires `asyncpg` + `SQLAlchemy async` session for production scale. |
| **In-process Circuit Breaker state** | State is not shared across multiple gateway replicas. Each replica maintains independent failure counts; the circuit may be open on one instance and closed on another. A distributed state store (Redis) would be required for true fleet-wide protection. |
| **Real-time balance calculation** | `_calculate_balance` fetches and sums