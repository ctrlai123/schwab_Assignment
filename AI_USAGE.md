# AI-Assisted Engineering — How Claude Code Was Used

This document describes how **Claude Code** (Anthropic's AI coding assistant) was used as an active participant across the full SDLC for this assignment.

---

## Tool used

**Claude Code** — AI coding assistant running inside VS Code  
Model: `claude-sonnet-4-6`  
Mode: Interactive agent with file read/write, terminal execution, and web search capabilities

---

## Design Agent

Claude Code acted as a design agent to:

- **Analyse the spec** — parsed `event-ledger-candidate-handout.md`, identified all requirements, constraints, and edge cases across Architecture, Requirements, and Constraints sections
- **Produce the system architecture** — designed the two-service split (public Gateway + internal Account Service), defined service boundaries, and decided on synchronous REST communication
- **Generate `DESIGN.md`** — produced the full design document including:
  - Mermaid system architecture diagram
  - Sequence diagrams for `POST /events` and `GET /balance` flows
  - Circuit breaker state machine diagram
  - ER diagrams for both database schemas
  - API contract table
  - Key design decisions table with rationale
- **Planned data models** — decided on no separate `accounts` table; balance computed from raw transactions to stay consistent
- **Identified architectural constraints** — flagged that Account Service must have no host port mapping in docker-compose (enforced at the infrastructure level, not just by convention)

---

## Development Agent

Claude Code acted as a development agent to:

- **Scaffold both services** — generated all FastAPI application code, SQLAlchemy models, Pydantic schemas, and routing for both `account-service` and `event-gateway`
- **Implement structured logging** — designed and wrote `JSONFormatter` used by both services; all log lines emit `timestamp`, `level`, `service`, `message`, `trace_id` as a JSON object
- **Implement distributed tracing** — designed the `X-Trace-Id` propagation pattern; identified and fixed the critical bug where middleware and handlers each generated independent UUIDs; single authoritative `trace_id` stored in `request.state`
- **Implement resiliency patterns** — wrote the thread-safe `CircuitBreaker` class (CLOSED → OPEN → HALF_OPEN) and wired it with retry + exponential back-off + timeout in `account_client.py`
- **Implement idempotency** — enforced at the database level via `eventId` as primary key; duplicate submissions return `200` with the original record
- **Implement graceful degradation** — GET endpoints serve from the Gateway's own DB when Account Service is down; `GET /accounts/{id}/balance` returns `503` with a clear human-readable message
- **Generate meaningful git commits** — planned and created 7 commits that tell the development story (scaffold → account-service → gateway → tests → docker → docs → fixes)
- **Audit against spec** — performed multiple rounds of spec compliance checks across Architecture, Requirements, and Constraints sections; identified and fixed violations (port 8001 exposure, duplicate status code, missing balance proxy)
- **Write `.dockerignore`** — identified that local `*.db` test files were leaking into Docker images via `COPY . .`; added exclusions to both services

---

## QA Agent

Claude Code acted as a QA agent to:

- **Write unit tests** — generated full test suites for both services using `pytest` and FastAPI's `TestClient`:
  - `account-service/tests/test_accounts.py` — 10 tests
  - `event-gateway/tests/test_events.py` — 17 tests
  - `event-gateway/tests/test_resiliency.py` — 10 tests
- **Generate coverage reports** — ran `pytest-cov` and produced HTML coverage reports under `docs/coverage/`
- **Write resiliency tests** — tests cover circuit breaker state transitions (CLOSED → OPEN → HALF_OPEN → CLOSED), 503 propagation, GET fallback when Account Service is down
- **Write tracing tests** — tests verify trace ID is echoed in response headers, forwarded to Account Service, and that auto-generated IDs are consistent across middleware and handler (catching the bug before it shipped)
- **Run functional tests live** — executed a full live test suite against the running Docker containers, covering:
  - CREDIT / DEBIT posting
  - Idempotency (duplicate event returns `200`)
  - Out-of-order chronological ordering
  - Balance calculation
  - Trace ID propagation
  - Validation errors (zero/negative amount, invalid type, missing fields)
  - Port 8001 inaccessibility from host
  - Structured JSON log output
- **Generate functional test report** — documented all live test results in `docs/FUNCTIONAL_TEST_REPORT.md`

---

## Summary

| SDLC phase | AI contribution |
|---|---|
| **Design** | Spec analysis, architecture decisions, design document, Mermaid diagrams |
| **Development** | All application code, logging, tracing, resiliency, idempotency, Docker config, git history |
| **QA** | Unit tests, coverage reports, functional test report, spec compliance audits |

Claude Code was used as a collaborative pair-programmer and technical reviewer throughout — not as a one-shot code generator. The AI iteratively identified gaps, fixed violations, and validated the solution against the original specification.
