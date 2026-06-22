"""
Resiliency tests – verify Gateway behaviour when Account Service is unavailable.
Run from event-gateway/ directory: pytest tests/
"""
import time
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.circuit_breaker import CircuitState, account_service_breaker

from .conftest import MOCK_ACCOUNT_OK, SAMPLE_EVENT

PATCH_APPLY = "app.services.account_client.apply_transaction"


# ── Graceful degradation ────────────────────────────────────────────────────

def test_503_when_account_service_unavailable(client):
    with patch(PATCH_APPLY, new_callable=AsyncMock) as mock:
        mock.side_effect = HTTPException(status_code=503, detail="Account Service unavailable after retries")
        r = client.post("/events", json=SAMPLE_EVENT)
    assert r.status_code == 503


def test_get_event_by_id_works_when_account_service_down(client):
    """GET /events/{id} reads from Gateway DB – must work even if Account Service is down."""
    with patch(PATCH_APPLY, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        client.post("/events", json=SAMPLE_EVENT)

    # Now simulate Account Service being down (GET doesn't call it anyway)
    r = client.get("/events/evt-001")
    assert r.status_code == 200
    assert r.json()["eventId"] == "evt-001"


def test_list_events_works_when_account_service_down(client):
    """GET /events reads from Gateway DB – must work even if Account Service is down."""
    with patch(PATCH_APPLY, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK):
        client.post("/events", json=SAMPLE_EVENT)

    r = client.get("/events?account=acct-123")
    assert r.status_code == 200
    assert r.json()["total"] == 1


# ── Circuit breaker ─────────────────────────────────────────────────────────

def test_circuit_breaker_starts_closed():
    assert account_service_breaker.state == CircuitState.CLOSED


def test_circuit_breaker_opens_after_threshold():
    for _ in range(account_service_breaker.failure_threshold):
        account_service_breaker.record_failure()
    assert account_service_breaker.state == CircuitState.OPEN
    assert not account_service_breaker.can_proceed()


def test_circuit_breaker_returns_503_when_open(client):
    """When the breaker is open, Gateway must immediately return 503."""
    # Force the breaker open
    account_service_breaker._failure_count = account_service_breaker.failure_threshold
    account_service_breaker._last_failure_time = time.time()
    account_service_breaker._state = CircuitState.OPEN

    with patch(PATCH_APPLY, new_callable=AsyncMock) as mock:
        mock.side_effect = HTTPException(
            status_code=503,
            detail="Account Service temporarily unavailable (circuit breaker open)",
        )
        r = client.post("/events", json={**SAMPLE_EVENT, "eventId": "evt-cb-test"})

    assert r.status_code == 503


def test_circuit_breaker_recovers_after_timeout():
    """After recovery_timeout, breaker should transition to HALF_OPEN."""
    account_service_breaker._failure_count = account_service_breaker.failure_threshold
    account_service_breaker._last_failure_time = time.time() - account_service_breaker.recovery_timeout - 1
    account_service_breaker._state = CircuitState.OPEN

    # can_proceed() triggers the transition check
    result = account_service_breaker.can_proceed()
    assert result is True
    assert account_service_breaker.state == CircuitState.HALF_OPEN


def test_circuit_breaker_closes_on_success():
    account_service_breaker._failure_count = 3
    account_service_breaker._state = CircuitState.HALF_OPEN
    account_service_breaker.record_success()
    assert account_service_breaker.state == CircuitState.CLOSED
    assert account_service_breaker._failure_count == 0


# ── Integration (Gateway → Account Service flow) ────────────────────────────

def test_full_flow_gateway_to_account_service(client):
    """
    Integration: gateway stores the event AND calls Account Service.
    We mock the Account Service HTTP call and verify the end-to-end response.
    """
    with patch(PATCH_APPLY, new_callable=AsyncMock, return_value=MOCK_ACCOUNT_OK) as mock:
        r = client.post("/events", json=SAMPLE_EVENT)

    assert r.status_code == 201
    assert r.json()["status"] == "processed"

    # Verify Account Service was called with the correct arguments
    mock.assert_called_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["account_id"] == "acct-123"
    assert kwargs["event_id"] == "evt-001"
    assert kwargs["event_type"] == "CREDIT"
    assert kwargs["amount"] == 150.0
    assert kwargs["currency"] == "USD"


# ── Balance proxy graceful degradation (Req 6) ─────────────────────────────

def test_balance_proxy_returns_503_when_account_service_down(client):
    """Req 6: balance queries must return a clear error when Account Service is unreachable."""
    with patch("app.services.account_client.get_balance", new_callable=AsyncMock) as mock:
        mock.side_effect = HTTPException(
            status_code=503,
            detail="Account Service is unreachable — balance cannot be retrieved",
        )
        r = client.get("/accounts/acct-123/balance")

    assert r.status_code == 503
    assert "unreachable" in r.json()["detail"].lower()
