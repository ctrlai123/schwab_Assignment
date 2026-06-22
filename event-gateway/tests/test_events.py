"""
Event Gateway – core functionality tests.
Run from event-gateway/ directory: pytest tests/
"""
from unittest.mock import AsyncMock, patch

from .conftest import MOCK_ACCOUNT_OK, SAMPLE_EVENT

PATCH_TARGET = "app.services.account_client.apply_transaction"


# ── Core functionality ──────────────────────────────────────────────────────

def test_create_event_success(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        r = client.post("/events", json=SAMPLE_EVENT)
    assert r.status_code == 201
    d = r.json()
    assert d["eventId"] == "evt-001"
    assert d["accountId"] == "acct-123"
    assert d["status"] == "processed"


def test_idempotency_second_submission_returns_original(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK) as mock:
        client.post("/events", json=SAMPLE_EVENT)
        r = client.post("/events", json=SAMPLE_EVENT)

    assert r.status_code == 200   # 200 = existing record returned, not newly created
    assert r.json()["eventId"] == "evt-001"
    assert r.json()["amount"] == 150.0   # original amount, not the 9999 in the duplicate
    # Account Service called exactly once despite two POST requests
    assert mock.call_count == 1


def test_out_of_order_events_listed_chronologically(client):
    events = [
        {**SAMPLE_EVENT, "eventId": "evt-003", "eventTimestamp": "2026-05-15T16:00:00Z"},
        {**SAMPLE_EVENT, "eventId": "evt-001", "eventTimestamp": "2026-05-15T14:00:00Z"},
        {**SAMPLE_EVENT, "eventId": "evt-002", "eventTimestamp": "2026-05-15T15:00:00Z"},
    ]
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        for ev in events:
            client.post("/events", json=ev)

    r = client.get("/events?account=acct-123")
    assert r.status_code == 200
    timestamps = [e["eventTimestamp"] for e in r.json()["events"]]
    assert timestamps == sorted(timestamps), "Events must be ordered by eventTimestamp"


def test_get_event_by_id(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        client.post("/events", json=SAMPLE_EVENT)

    r = client.get("/events/evt-001")
    assert r.status_code == 200
    assert r.json()["eventId"] == "evt-001"


def test_get_nonexistent_event_returns_404(client):
    r = client.get("/events/does-not-exist")
    assert r.status_code == 404


def test_list_events_filter_by_account(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        client.post("/events", json=SAMPLE_EVENT)
        client.post("/events", json={**SAMPLE_EVENT, "eventId": "evt-other", "accountId": "acct-999"})

    r = client.get("/events?account=acct-123")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["events"][0]["accountId"] == "acct-123"


# ── Validation ──────────────────────────────────────────────────────────────

def test_missing_required_fields(client):
    r = client.post("/events", json={"eventId": "evt-x"})
    assert r.status_code == 422


def test_invalid_event_type(client):
    r = client.post("/events", json={**SAMPLE_EVENT, "type": "TRANSFER"})
    assert r.status_code == 422


def test_zero_amount(client):
    r = client.post("/events", json={**SAMPLE_EVENT, "amount": 0})
    assert r.status_code == 422


def test_negative_amount(client):
    r = client.post("/events", json={**SAMPLE_EVENT, "amount": -50})
    assert r.status_code == 422


def test_invalid_currency_length(client):
    r = client.post("/events", json={**SAMPLE_EVENT, "currency": "DOLLARS"})
    assert r.status_code == 422


# ── Tracing ─────────────────────────────────────────────────────────────────

def test_trace_id_echoed_in_response_header(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        r = client.post("/events", json=SAMPLE_EVENT, headers={"x-trace-id": "trace-abc-123"})
    assert r.status_code == 201
    assert r.headers.get("x-trace-id") == "trace-abc-123"


def test_trace_id_propagated_to_account_service(client):
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK) as mock:
        client.post("/events", json=SAMPLE_EVENT, headers={"x-trace-id": "trace-propagate"})

    mock.assert_called_once()
    assert mock.call_args.kwargs["trace_id"] == "trace-propagate"


def test_health_check(client):
    with patch("app.services.account_client.check_health", new_callable=AsyncMock, return_value={"status": "healthy"}):
        r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["service"] == "event-gateway"
    assert "circuit_breaker" in d
    assert "metrics" in d


# ── Balance proxy (Req 6 + Req 8) ──────────────────────────────────────────

MOCK_BALANCE = {"accountId": "acct-123", "balance": 400.0, "currency": "USD", "transactionCount": 2}

def test_balance_proxy_returns_balance(client):
    with patch("app.services.account_client.get_balance", new_callable=AsyncMock, return_value=MOCK_BALANCE):
        r = client.get("/accounts/acct-123/balance")
    assert r.status_code == 200
    assert r.json()["balance"] == 400.0
    assert r.json()["accountId"] == "acct-123"


def test_trace_id_consistent_across_middleware_and_handler(client):
    """Req 3: trace ID must be the same in middleware logs and in the call to Account Service."""
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK) as mock:
        r = client.post("/events", json=SAMPLE_EVENT, headers={"x-trace-id": "consistent-trace"})

    assert r.status_code == 201
    # Same trace_id echoed back in response header (set by middleware)
    assert r.headers.get("x-trace-id") == "consistent-trace"
    # Same trace_id forwarded to Account Service
    assert mock.call_args.kwargs["trace_id"] == "consistent-trace"


def test_trace_id_auto_generated_and_consistent(client):
    """When client sends no X-Trace-Id, the generated ID must be the same in header and Account Service call."""
    with patch(PATCH_TARGET, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK) as mock:
        r = client.post("/events", json={**SAMPLE_EVENT, "eventId": "evt-auto-trace"})

    assert r.status_code == 201
    response_trace_id = r.headers.get("x-trace-id")
    assert response_trace_id is not None
    # The same auto-generated trace_id must reach the Account Service
    assert mock.call_args.kwargs["trace_id"] == response_trace_id
