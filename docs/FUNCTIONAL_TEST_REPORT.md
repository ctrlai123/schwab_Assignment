# Functional Test Report

**Date:** 2026-06-22  
**Environment:** Docker Compose (`docker compose up --build`)  
**Gateway:** http://localhost:8000  
**Account Service:** internal only (no host port)  

---

## Test Results Summary

| # | Scenario | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | Health check — both services + circuit breaker | 200, state=CLOSED | 200, state=CLOSED | PASS |
| 2 | POST CREDIT event (new) | 201 Created | 201 Created | PASS |
| 3 | POST DEBIT event (out-of-order earlier timestamp) | 201 Created | 201 Created | PASS |
| 4 | POST duplicate event (same eventId, different amount) | 200 OK, original amount returned | 200 OK, original 500.0 returned | PASS |
| 5 | GET /events?account= — chronological order | evt-002 (08:00) before evt-001 (10:00) | evt-002 first | PASS |
| 6 | GET /accounts/{id}/balance — correct math | 500 − 150 = 350 USD | 350.0 USD | PASS |
| 7 | Trace ID echoed in response header | x-trace-id = sent value | x-trace-id = my-custom-trace-abc | PASS |
| 8a | Validation — zero amount | 422 | 422 | PASS |
| 8b | Validation — negative amount | 422 | 422 | PASS |
| 8c | Validation — invalid event type | 422 | 422 | PASS |
| 8d | Validation — missing required field | 422 | 422 | PASS |
| 9 | GET /events/{id} — existing event | 200, correct fields | 200 | PASS |
| 10 | GET /events/{id} — nonexistent id | 404 | 404 | PASS |
| 11 | Port 8001 unreachable from host | connection refused | connection refused | PASS |
| 12 | Structured JSON logs — fields present | timestamp, level, service, message, trace_id | all fields present | PASS |

**Total: 15/15 PASS**

---

## Detailed Results

### 1. Health Check

```json
GET http://localhost:8000/health → 200
{
  "status": "healthy",
  "service": "event-gateway",
  "database": "healthy",
  "account_service": {
    "status": "healthy",
    "service": "account-service",
    "database": "healthy"
  },
  "circuit_breaker": {
    "state": "CLOSED",
    "failure_count": 0,
    "last_failure_time": null
  },
  "metrics": { "request_counts": {}, "error_counts": {} }
}
```

### 2. POST CREDIT event

```
POST /events  →  201 Created
{
  "eventId": "evt-001", "accountId": "acct-123",
  "type": "CREDIT", "amount": 500.0, "currency": "USD",
  "eventTimestamp": "2026-05-15T10:00:00", "status": "processed"
}
```

### 3. POST DEBIT event (arrived later, earlier timestamp)

```
POST /events  →  201 Created
{
  "eventId": "evt-002", "type": "DEBIT", "amount": 150.0,
  "eventTimestamp": "2026-05-15T08:00:00", "status": "processed"
}
```

### 4. Idempotency — duplicate eventId

Re-submitted `evt-001` with `amount: 9999.99` (changed).

```
POST /events  →  200 OK          ← 200, not 201
{
  "eventId": "evt-001",
  "amount": 500.0               ← original amount; 9999.99 was ignored
}
Account Service call count: 1   ← called only once across both submissions
```

### 5. Out-of-order chronological ordering

```
GET /events?account=acct-123  →  200 OK
events[0]: evt-002  2026-05-15T08:00:00  DEBIT   150.0   ← earlier timestamp first
events[1]: evt-001  2026-05-15T10:00:00  CREDIT  500.0
total: 2
```

### 6. Balance calculation

```
GET /accounts/acct-123/balance  →  200 OK
{
  "accountId": "acct-123",
  "balance": 350.0,             ← 500 CREDIT − 150 DEBIT = 350
  "currency": "USD",
  "transactionCount": 2
}
```

### 7. Trace ID propagation

```
POST /events  (X-Trace-Id: my-custom-trace-abc)  →  201 Created
Response header: x-trace-id = my-custom-trace-abc   ← echoed exactly
```

### 8. Validation errors

| Input | HTTP Status |
|---|---|
| `amount: 0` | 422 |
| `amount: -50` | 422 |
| `type: "TRANSFER"` | 422 |
| Missing `eventId` | 422 |

### 9–10. Event retrieval

```
GET /events/evt-001          →  200  {eventId: "evt-001", type: "CREDIT", amount: 500.0, status: "processed"}
GET /events/does-not-exist   →  404
```

### 11. Account Service internal-only

```
GET http://localhost:8001/health  →  connection refused (no host port mapped)
```

### 12. Structured JSON logs (sample)

```json
{"timestamp": "2026-06-22T20:43:46Z", "level": "INFO",  "service": "event-gateway",   "message": "Processing event evt-001 for account acct-123", "trace_id": "a1b2c3..."}
{"timestamp": "2026-06-22T20:43:46Z", "level": "INFO",  "service": "account-service", "message": "Incoming request: POST /accounts/acct-123/transactions",  "trace_id": "a1b2c3..."}
{"timestamp": "2026-06-22T20:43:46Z", "level": "INFO",  "service": "event-gateway",   "message": "Event evt-001 processed successfully", "trace_id": "a1b2c3..."}
```

Both services emit the same `trace_id` for the same client request — confirming end-to-end trace correlation.
