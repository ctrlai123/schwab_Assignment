"""
HTTP client for the Account Service.

Resiliency pattern: Circuit Breaker + Timeout + Retry with exponential back-off.
  - Requests timeout after REQUEST_TIMEOUT seconds.
  - On connection/timeout errors we retry up to MAX_RETRIES times with
    exponential back-off before giving up.
  - Each failure increments the circuit breaker counter.  Once
    failure_threshold is reached the breaker opens and calls are
    rejected immediately (fast-fail) until recovery_timeout elapses.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx
from fastapi import HTTPException

from ..circuit_breaker import account_service_breaker

logger = logging.getLogger("event-gateway")

ACCOUNT_SERVICE_URL = os.getenv("ACCOUNT_SERVICE_URL", "http://localhost:8001")
REQUEST_TIMEOUT = 5.0
MAX_RETRIES = 3
# Back-off delays between retries (seconds)
BACKOFF = [0.5, 1.0, 2.0]


async def apply_transaction(
    *,
    account_id: str,
    event_id: str,
    event_type: str,
    amount: float,
    currency: str,
    event_timestamp: str,
    trace_id: Optional[str] = None,
) -> dict:
    if not account_service_breaker.can_proceed():
        logger.warning(
            "Circuit breaker OPEN – rejecting call for event %s", event_id,
            extra={"trace_id": trace_id},
        )
        raise HTTPException(
            status_code=503,
            detail="Account Service temporarily unavailable (circuit breaker open)",
        )

    headers = {"x-trace-id": trace_id or "unknown"}
    payload = {
        "eventId": event_id,
        "type": event_type,
        "amount": amount,
        "currency": currency,
        "eventTimestamp": event_timestamp,
    }

    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{ACCOUNT_SERVICE_URL}/accounts/{account_id}/transactions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

            account_service_breaker.record_success()
            logger.info(
                "Transaction %s applied to account %s", event_id, account_id,
                extra={"trace_id": trace_id},
            )
            return response.json()

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = exc
            account_service_breaker.record_failure()
            logger.warning(
                "Account Service unreachable (attempt %d/%d): %s",
                attempt + 1, MAX_RETRIES, exc,
                extra={"trace_id": trace_id},
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF[attempt])

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                # 4xx – client error, don't retry, don't trip the breaker
                account_service_breaker.record_success()
                raise HTTPException(
                    status_code=exc.response.status_code,
                    detail=exc.response.json(),
                )
            last_error = exc
            account_service_breaker.record_failure()
            logger.error(
                "Account Service server error (attempt %d/%d): %s",
                attempt + 1, MAX_RETRIES, exc,
                extra={"trace_id": trace_id},
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF[attempt])

    raise HTTPException(
        status_code=503,
        detail=f"Account Service unavailable after {MAX_RETRIES} retries: {last_error}",
    )


async def get_balance(account_id: str, trace_id: Optional[str] = None) -> dict:
    if not account_service_breaker.can_proceed():
        raise HTTPException(
            status_code=503,
            detail="Account Service temporarily unavailable (circuit breaker open) — balance cannot be retrieved",
        )
    headers = {"x-trace-id": trace_id or "unknown"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(
                f"{ACCOUNT_SERVICE_URL}/accounts/{account_id}/balance",
                headers=headers,
            )
            r.raise_for_status()
            account_service_breaker.record_success()
            return r.json()
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        account_service_breaker.record_failure()
        raise HTTPException(
            status_code=503,
            detail=f"Account Service is unreachable — balance cannot be retrieved: {exc}",
        )
    except httpx.HTTPStatusError as exc:
        account_service_breaker.record_success()
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.json())


async def check_health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{ACCOUNT_SERVICE_URL}/health")
            return r.json()
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)}
