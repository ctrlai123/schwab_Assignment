"""
Account Service tests — idempotency, balance, out-of-order, tracing.
Run from account-service/ directory: pytest tests/
"""

SAMPLE_TX = {
    "eventId": "evt-001",
    "type": "CREDIT",
    "amount": 150.0,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11Z",
}


# ── Core functionality ──────────────────────────────────────────────────────

def test_apply_credit_transaction(client):
    r = client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    assert r.status_code == 200
    d = r.json()
    assert d["eventId"] == "evt-001"
    assert d["type"] == "CREDIT"
    assert d["amount"] == 150.0
    assert d["alreadyApplied"] is False


def test_apply_debit_transaction(client):
    client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    debit = {**SAMPLE_TX, "eventId": "evt-002", "type": "DEBIT", "amount": 50.0}
    r = client.post("/accounts/acct-123/transactions", json=debit)
    assert r.status_code == 200
    assert r.json()["type"] == "DEBIT"


def test_idempotency_same_event_twice(client):
    client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    r = client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    assert r.status_code == 200
    assert r.json()["alreadyApplied"] is True


def test_balance_correct(client):
    client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    client.post("/accounts/acct-123/transactions", json={**SAMPLE_TX, "eventId": "evt-002", "amount": 100.0})
    client.post("/accounts/acct-123/transactions", json={**SAMPLE_TX, "eventId": "evt-003", "type": "DEBIT", "amount": 75.0})

    r = client.get("/accounts/acct-123/balance")
    assert r.status_code == 200
    d = r.json()
    assert d["balance"] == 175.0  # 150 + 100 - 75
    assert d["transactionCount"] == 3


def test_balance_out_of_order_arrival(client):
    """Balance must be correct even when events arrive in the wrong order."""
    events = [
        {**SAMPLE_TX, "eventId": "evt-003", "type": "DEBIT",  "amount": 75.0,  "eventTimestamp": "2026-05-15T16:00:00Z"},
        {**SAMPLE_TX, "eventId": "evt-001", "type": "CREDIT", "amount": 150.0, "eventTimestamp": "2026-05-15T14:00:00Z"},
        {**SAMPLE_TX, "eventId": "evt-002", "type": "CREDIT", "amount": 100.0, "eventTimestamp": "2026-05-15T15:00:00Z"},
    ]
    for ev in events:
        client.post("/accounts/acct-123/transactions", json=ev)

    r = client.get("/accounts/acct-123/balance")
    assert r.status_code == 200
    assert r.json()["balance"] == 175.0


def test_get_account_details(client):
    client.post("/accounts/acct-123/transactions", json=SAMPLE_TX)
    r = client.get("/accounts/acct-123")
    assert r.status_code == 200
    d = r.json()
    assert d["accountId"] == "acct-123"
    assert len(d["recentTransactions"]) == 1


def test_account_not_found(client):
    r = client.get("/accounts/nonexistent")
    assert r.status_code == 404


def test_balance_not_found(client):
    r = client.get("/accounts/nonexistent/balance")
    assert r.status_code == 404


# ── Observability ───────────────────────────────────────────────────────────

def test_health_check(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


# ── Tracing ─────────────────────────────────────────────────────────────────

def test_trace_id_accepted(client):
    r = client.post(
        "/accounts/acct-123/transactions",
        json=SAMPLE_TX,
        headers={"x-trace-id": "trace-xyz-123"},
    )
    assert r.status_code == 200
