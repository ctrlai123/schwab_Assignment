"""
Simple thread-safe circuit breaker with three states:
  CLOSED   → calls pass through normally
  OPEN     → calls are immediately rejected
  HALF_OPEN → one probe call is allowed; success closes, failure re-opens
"""
import time
import threading
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger("event-gateway")


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = CircuitState.CLOSED
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def can_proceed(self) -> bool:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                elapsed = time.time() - (self._last_failure_time or 0)
                if elapsed >= self.recovery_timeout:
                    logger.info("Circuit breaker → HALF_OPEN", extra={"trace_id": None})
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    return True
                return False
            # HALF_OPEN
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    def record_success(self):
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("Circuit breaker → CLOSED", extra={"trace_id": None})
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if (
                self._failure_count >= self.failure_threshold
                or self._state == CircuitState.HALF_OPEN
            ):
                logger.warning(
                    "Circuit breaker → OPEN (failures: %d)", self._failure_count,
                    extra={"trace_id": None},
                )
                self._state = CircuitState.OPEN

    def get_status(self) -> dict:
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "last_failure_time": self._last_failure_time,
        }


# Module-level singleton used by the gateway
account_service_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
