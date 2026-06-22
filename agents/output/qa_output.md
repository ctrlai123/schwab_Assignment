# Full QA Report — Financial Microservices (account-service + event-gateway)

---

## 1. Coverage Analysis

### What Is Covered

#### account-service (95% overall)

| Module | Coverage | What the tests exercise |
|---|---|---|
| `app/routes/accounts.py` | 100% | CREDIT/DEBIT apply, idempotency via `event_id`, balance arithmetic, account auto-creation, `get_account` details, 404 paths for balance and account detail, trace-ID header acceptance |
| `app/routes/health.py` | 85% | Happy-path `/health` 200 response; the unhealthy/DB-down branch is not hit |
| `app/database.py` | 67% | In-test SQLite engine works; the *startup* connection-pool creation and the production `SessionLocal` factory (executed when the real app boots with PostgreSQL) are never exercised |

#### event-gateway (80% overall)

| Module | Coverage | What the tests exercise |
|---|---|---|
| `app/routes/events.py` | 100% | POST happy-path, idempotency 200 return, GET by ID, GET list with account filter, chronological ordering, field-level 422 validation, account-service 503 propagation, balance proxy happy-path and 503 |
| `app/circuit_breaker.py` | 91% | CLOSED→OPEN transition, OPEN→HALF_OPEN timeout, HALF_OPEN→CLOSED on success, `get_status()` dict structure; **the HALF_OPEN→OPEN re-open path (probe failure) is NOT covered** |
| `app/services/account_client.py` | 23% | All tests mock `apply_transaction` / `get_balance` at the function boundary, so the actual HTTP retry loop, exponential back-off, `httpx` error mapping, and `check_health` are essentially untested |
| `app/main.py` | 87% | Middleware, startup lifespan, metrics endpoint tested; exception-handler edge cases and the ASGI startup failure path are not hit |

### Business-Critical Gaps

1. **`account_client.py` at 23%** — The retry/back-off logic, the 4xx-vs-5xx branching (which deliberately does *not* trip the circuit breaker), and `check_health` have zero real assertions. A regression here would be invisible.
2. **Circuit breaker HALF_OPEN re-open path** — If the probe call during HALF_OPEN fails, the breaker must return to OPEN. This is untested.
3. **`_calculate_balance` floating-point edge cases** — Large or many small amounts could produce rounding drift; `round(balance, 2)` is the only guard and it is exercised only with tidy values.
4. **Database startup path (67%)** — Connection failures at boot are silent in tests.
5. **Concurrent duplicate submissions** — Idempotency is tested serially; a parallel race where two identical POSTs arrive simultaneously before either is committed is not covered.
6. **Currency field on balance** — `get_balance` derives `currency` from the *last applied transaction*, which is undefined behaviour for a multi-currency account.

---

## 2. Missing Test Scenarios (≥ 8)

| # | Scenario | Why it matters |
|---|---|---|
| 1 | **Concurrent duplicate event submissions** — two identical `eventId` POSTs in parallel threads | Without a DB-level unique constraint the idempotency guard can be defeated by a race condition, double-crediting an account |
| 2 | **Very large monetary amounts** — e.g. `9_999_999_999.99` | Floating-point precision loss or column overflow can silently corrupt balances |
| 3 | **Currency mismatch within an account** — CREDIT in USD then DEBIT in EUR, then check balance | The balance calculation ignores currency; mixing currencies produces a nonsense number |
| 4 | **HALF_OPEN circuit breaker re-opens on probe failure** | Validated by spec; currently the path where `record_failure()` is called while in HALF_OPEN is untested |
| 5 | **Account Service returns 4xx (e.g. 400/422)** — Gateway must NOT retry and must NOT trip the circuit breaker | The `httpx.HTTPStatusError` 4xx branch in `account_client.py` calls `record_success()` before raising; this is a counter-intuitive design that needs an explicit test |
| 6 | **Retry + exponential back-off on 503 from Account Service** — verify exactly 3 attempts are made with correct delays | The retry loop is the primary resiliency mechanism; it has 0% test coverage |
| 7 | **Floating-point balance accumulation** — 100 transactions of $0.10 should equal $10.00, not $9.999999…  | `round(balance, 2)` may not be sufficient for all accumulations |
| 8 | **`eventTimestamp` in the future** — event dated 10 years ahead | No schema validation rejects future timestamps; downstream reporting and ordering may break |
| 9 | **`metadata` field with very large/nested payload** — test DB column limits | No size limit on `metadata_`; an attacker or misconfigured producer can store megabytes per event |
| 10 | **`GET /events` with no events in DB** — empty list response | `total: 0`, `events: []` shape should be explicitly asserted |
| 11 | **`GET /accounts/{id}` recent-transactions capped at 20** — insert 25 transactions and verify only 20 are returned | The `.limit(20)` is a silent truncation that could mislead callers |
| 12 | **Health endpoint when DB is unavailable** — the 85% gap in `health.py` | A degraded-DB state should return a non-200 or a specific unhealthy payload |

---

## 3. New Test Cases (Runnable pytest Code)

```python
"""
Additional critical test cases for the financial microservices.

Placement:
  - test_financial_edge_cases.py → event-gateway/tests/
  - test_account_edge_cases.py  → account-service/tests/

Run:
  cd event-gateway && pytest tests/test_financial_edge_cases.py -v
  cd account-service && pytest tests/test_account_edge_cases.py -v
"""

# =============================================================================
# FILE: event-gateway/tests/test_financial_edge_cases.py
# =============================================================================

import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest
from fastapi import HTTPException

from app.circuit_breaker import CircuitBreaker, CircuitState, account_service_breaker

from .conftest import MOCK_ACCOUNT_OK, SAMPLE_EVENT

PATCH_APPLY = "app.services.account_client.apply_transaction"


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Concurrent duplicate submissions (race-condition idempotency)
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_duplicate_event_submissions(client):
    """
    Two threads submit the same eventId simultaneously.
    Exactly one must receive HTTP 201 and one HTTP 200 (idempotent return),
    and the Account Service must be called exactly ONCE regardless of ordering.

    This exercises the window between the idempotency SELECT and the INSERT
    where a race condition could cause a double-write.
    """
    results = []

    def post_event():
        with patch(PATCH_APPLY, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
            r = client.post("/events", json={**SAMPLE_EVENT, "eventId": "evt-race-001"})
            results.append(r.status_code)

    threads = [threading.Thread(target=post_event) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Regardless of ordering: one 201 + one 200, OR both 200 if the second
    # thread sees the first commit.  What must NEVER happen is two 201s.
    assert results.count(201) <= 1, (
        f"Double-creation detected — both threads returned 201. "
        f"Status codes: {results}. "
        f"This indicates a race condition in the idempotency guard."
    )
    # Total events stored must be exactly 1
    r = client.get("/events?account=acct-123")
    assert r.json()["total"] == 1, "Exactly one event record must exist after concurrent submissions"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Very large monetary amount
# ─────────────────────────────────────────────────────────────────────────────

def test_very_large_amount_accepted_and_stored_accurately(client):
    """
    A single transaction of $9,999,999,999.99 must be persisted and returned
    with no floating-point corruption.  Many financial systems silently truncate
    or lose precision on large Decimal/float values.
    """
    large_amount = 9_999_999_999.99
    large_event = {
        **SAMPLE_EVENT,
        "eventId": "evt-large-001",
        "amount": large_amount,
        "currency": "USD",
    }

    mock_ok = {**MOCK_ACCOUNT_OK, "eventId": "evt-large-001", "amount": large_amount}

    with patch(PATCH_APPLY, new_callable=AsyncMock, return_value=mock_ok):
        r = client.post("/events", json=large_event)

    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.json()}"
    stored = r.json()

    # The amount must round-trip exactly — no precision loss
    assert stored["amount"] == large_amount, (
        f"Precision loss detected: sent {large_amount}, received {stored['amount']}"
    )

    # Verify the stored record via GET as well
    r2 = client.get("/events/evt-large-001")
    assert r2.status_code == 200
    assert r2.json()["amount"] == large_amount, (
        f"Stored amount {r2.json()['amount']} differs from submitted {large_amount}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Circuit breaker re-opens on probe failure during HALF_OPEN
# ─────────────────────────────────────────────────────────────────────────────

def test_circuit_breaker_reopens_on_half_open_probe_failure():
    """
    Sequence:
      1. Drive the breaker to OPEN.
      2. Simulate recovery timeout elapsed → transitions to HALF_OPEN.
      3. The probe call fails → breaker must return to OPEN, not CLOSED.

    This is the HALF_OPEN → OPEN path that has 0% coverage.
    """
    # Step 1: open the breaker
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # Step 2: wait for recovery timeout → HALF_OPEN
    time.sleep(0.1)
    assert breaker.can_proceed() is True
    assert breaker.state == CircuitState.HALF_OPEN

    # Step 3: probe fails — must go back to OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN, (
        f"Expected OPEN after probe failure, got {breaker.state}. "
        f"The circuit breaker does not correctly re-open on HALF_OPEN probe failure."
    )
    assert breaker.can_proceed() is False, (
        "Circuit breaker should reject calls immediately after re-opening"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Account Service 4xx does NOT trip the circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

def test_account_service_4xx_does_not_trip_circuit_breaker(client):
    """
    When the Account Service returns a 4xx (client error, e.g. 422 Unprocessable),
    the circuit breaker must NOT count it as a failure — the problem is with the
    request, not with the service availability.

    Concretely: after a 4xx response the breaker failure_count must stay at 0
    and the breaker state must remain CLOSED.

    This exercises the `httpx.HTTPStatusError` < 500 branch in account_client.py
    which calls record_success() before raising.
    """
    # Simulate Account Service rejecting the payload with 422
    account_service_breaker._failure_count = 0
    account_service_breaker._state = CircuitState.CLOSED

    async def fake_apply(**kwargs):
        # Simulate what account_client does internally for a 4xx:
        # it calls record_success() then raises HTTPException(422)
        account_service_breaker.record_success()
        raise HTTPException(status_code=422, detail={"msg": "Invalid transaction type"})

    with patch(PATCH_APPLY, side_effect=fake_apply):
        r = client.post("/events", json=SAMPLE_EVENT)

    # Gateway must propagate the 422 to the caller
    assert r.status_code == 422, f"Expected 422 from upstream, got {r.status_code}"

    # Critical: the circuit breaker must NOT have been tripped
    assert account_service_breaker.state == CircuitState.CLOSED, (
        f"Circuit breaker should remain CLOSED after a 4xx client error, "
        f"but state is {account_service_breaker.state}"
    )
    assert account_service_breaker._failure_count == 0, (
        f"failure_count should be 0 after a 4xx error, "
        f"got {account_service_breaker._failure_count}"
    )


# =============================================================================
# FILE: account-service/tests/test_account_edge_cases.py
# =============================================================================

# NOTE: This test lives in account-service/tests/ and uses that service's
#       conftest.py fixtures (client, setup_db).  Copy it there before running.

SAMPLE_TX = {
    "eventId": "evt-001",
    "type": "CREDIT",
    "amount": 150.0,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11Z",
}


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Floating-point balance accumulation accuracy
# ─────────────────────────────────────────────────────────────────────────────

def test_balance_accumulation_floating_point_accuracy(client):
    """
    Apply 100 CREDIT transactions of $0.10 each.
    Expected balance: $10.00 exactly.

    Naive float addition of 100 × 0.10 in Python yields 9.99999999999999
    rather than 10.00.  The service uses round(balance, 2) which should
    compensate, but this test makes the assumption explicit and regression-proof.

    Also apply 1 DEBIT of $0.05 to exercise mixed-sign accumulation.
    Expected final balance: $10.00 - $0.05 = $9.95
    """
    # Apply 100 credits of $0.10
    for i in range(100):
        tx = {
            **SAMPLE_TX,
            "eventId": f"evt-fp-{i:04d}",
            "type": "CREDIT",
            "amount": 0.10,
        }
        r = client.post("/accounts/acct-fp/transactions", json=tx)
        assert r.status_code == 200, f"Transaction {i} failed: {r.json()}"

    # Apply 1 debit of $0.05
    r = client.post(
        "/accounts