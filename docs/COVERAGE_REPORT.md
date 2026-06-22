# Test Coverage Report

**Generated:** 2026-06-22  
**Tool:** pytest-cov 7.1.0 / coverage.py  
**Python:** 3.11.4  

HTML reports: `docs/coverage/account-service/index.html` and `docs/coverage/event-gateway/index.html`

---

## Account Service — 95% overall

```
Name                     Stmts   Miss  Cover   Missing
------------------------------------------------------
app/__init__.py              0      0   100%
app/database.py             12      4    67%   18-22  (startup DB init path)
app/logging_config.py       19      1    95%   20
app/main.py                 23      1    96%   39
app/models.py               16      0   100%
app/routes/__init__.py       0      0   100%
app/routes/accounts.py      63      0   100%   ← all business logic covered
app/routes/health.py        13      2    85%   14-15
app/schemas.py              29      0   100%
------------------------------------------------------
TOTAL                      175      8    95%
```

Tests: **10 passed** in 1.04 s

| Test | What it covers |
|---|---|
| `test_apply_credit_transaction` | CREDIT posted, stored, balance updated |
| `test_apply_debit_transaction` | DEBIT posted, stored, balance updated |
| `test_idempotency_same_event_twice` | Same eventId submitted twice — processed once |
| `test_balance_correct` | CREDIT − DEBIT math |
| `test_balance_out_of_order_arrival` | Events arriving out of timestamp order |
| `test_get_account_details` | Full transaction list for account |
| `test_account_not_found` | 404 for unknown account |
| `test_balance_not_found` | 404 balance for unknown account |
| `test_health_check` | /health returns healthy |
| `test_trace_id_accepted` | X-Trace-Id header accepted and propagated |

---

## Event Gateway — 80% overall

```
Name                             Stmts   Miss  Cover   Missing
--------------------------------------------------------------
app/__init__.py                      0      0   100%
app/circuit_breaker.py              55      5    91%   46, 56-59
app/database.py                     12      4    67%   18-22  (startup DB init path)
app/logging_config.py               19      1    95%   20
app/main.py                         31      4    87%   45-47, 59
app/metrics.py                       9      0   100%
app/models.py                       14      0   100%
app/routes/__init__.py               0      0   100%
app/routes/events.py                69      0   100%   ← all business logic covered
app/routes/health.py                17      2    88%   18-19
app/schemas.py                      54      2    96%   19, 26
app/services/__init__.py             0      0   100%
app/services/account_client.py      66     51    23%   41-107 (HTTP retry/timeout)
--------------------------------------------------------------
TOTAL                              346     69    80%
```

Tests: **27 passed** in 1.61 s

| Test file | Tests | What it covers |
|---|---|---|
| `test_events.py` | 17 | Core flows, validation, tracing, balance proxy |
| `test_resiliency.py` | 10 | Circuit breaker states, 503 propagation, GET fallback, integration |

### Note on account_client.py coverage (23%)

`account_client.py` contains the actual HTTP retry + timeout + circuit breaker logic that calls the real Account Service network. Its coverage is low because these tests use `unittest.mock.AsyncMock` to patch the function at the call site — the internal retry loop and `httpx` calls are not exercised by unit tests.

This is correct testing practice: unit tests mock external I/O. The actual network retry behaviour is validated by the live functional tests (see `docs/FUNCTIONAL_TEST_REPORT.md`), which run against real Docker containers.

---

## Combined

| Service | Tests | Coverage |
|---|---|---|
| Account Service | 10 | **95%** |
| Event Gateway | 27 | **80%** |
| **Total** | **37** | **~84%** |
