import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db
from app.circuit_breaker import account_service_breaker, CircuitState

TEST_DB_URL = "sqlite:///./test_gateway.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    account_service_breaker._failure_count = 0
    account_service_breaker._last_failure_time = None
    account_service_breaker._state = CircuitState.CLOSED
    account_service_breaker._half_open_calls = 0
    yield


@pytest.fixture
def client():
    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# Reusable mock response from Account Service
MOCK_ACCOUNT_OK = {
    "eventId": "evt-001",
    "accountId": "acct-123",
    "type": "CREDIT",
    "amount": 150.0,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11",
    "appliedAt": "2026-06-01T10:00:00",
    "alreadyApplied": False,
}

SAMPLE_EVENT = {
    "eventId": "evt-001",
    "accountId": "acct-123",
    "type": "CREDIT",
    "amount": 150.00,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11Z",
    "metadata": {"source": "mainframe-batch", "batchId": "B-9042"},
}
