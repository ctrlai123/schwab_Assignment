import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..metrics import track
from ..models import Event
from ..schemas import EventCreate, EventListResponse, EventResponse
from ..services import account_client

router = APIRouter()
logger = logging.getLogger("event-gateway")


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _to_response(event: Event) -> EventResponse:
    return EventResponse(
        eventId=event.event_id,
        accountId=event.account_id,
        type=event.type,
        amount=event.amount,
        currency=event.currency,
        eventTimestamp=event.event_timestamp,
        metadata=event.metadata_,
        receivedAt=event.received_at,
        status=event.status,
    )


@router.post("/events", response_model=EventResponse, status_code=201)
async def create_event(
    request: Request,
    event: EventCreate,
    db: Session = Depends(get_db),
):
    trace_id = request.state.trace_id
    extra = {"trace_id": trace_id}
    track("POST /events")

    logger.info("Processing event %s for account %s", event.eventId, event.accountId, extra=extra)

    # ── Idempotency check ──────────────────────────────────────────────────
    existing = db.query(Event).filter(Event.event_id == event.eventId).first()
    if existing:
        logger.info("Duplicate event %s – returning stored record", event.eventId, extra=extra)
        from fastapi.responses import JSONResponse
        from fastapi.encoders import jsonable_encoder
        return JSONResponse(status_code=200, content=jsonable_encoder(_to_response(existing)))

    # ── Persist with status=pending ────────────────────────────────────────
    db_event = Event(
        event_id=event.eventId,
        account_id=event.accountId,
        type=event.type,
        amount=event.amount,
        currency=event.currency,
        event_timestamp=_naive(event.eventTimestamp),
        metadata_=event.metadata,
        received_at=_naive(datetime.now(timezone.utc)),
        status="pending",
    )
    db.add(db_event)
    db.commit()

    # ── Call Account Service ───────────────────────────────────────────────
    try:
        await account_client.apply_transaction(
            account_id=event.accountId,
            event_id=event.eventId,
            event_type=event.type,
            amount=event.amount,
            currency=event.currency,
            event_timestamp=event.eventTimestamp.isoformat(),
            trace_id=trace_id,
        )
        db_event.status = "processed"
        db.commit()
        db.refresh(db_event)
        logger.info("Event %s processed successfully", event.eventId, extra=extra)
    except HTTPException as exc:
        db_event.status = "failed"
        db.commit()
        track("POST /events", error=True)
        logger.error("Event %s failed: %s", event.eventId, exc.detail, extra=extra)
        raise

    return _to_response(db_event)


@router.get("/events/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    trace_id = request.state.trace_id
    track("GET /events/{id}")
    logger.info("Fetching event %s", event_id, extra={"trace_id": trace_id})

    ev = db.query(Event).filter(Event.event_id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    return _to_response(ev)


@router.get("/events", response_model=EventListResponse)
async def list_events(
    request: Request,
    account: Optional[str] = Query(None, description="Filter by accountId"),
    db: Session = Depends(get_db),
):
    trace_id = request.state.trace_id
    track("GET /events")
    logger.info("Listing events for account=%s", account, extra={"trace_id": trace_id})

    q = db.query(Event)
    if account:
        q = q.filter(Event.account_id == account)

    # chronological order by event timestamp (not arrival order)
    events = q.order_by(Event.event_timestamp.asc()).all()
    return EventListResponse(events=[_to_response(e) for e in events], total=len(events))


@router.get("/accounts/{account_id}/balance")
async def get_account_balance(
    account_id: str,
    request: Request,
):
    """
    Proxy to Account Service balance endpoint.
    Returns 503 with a clear message if Account Service is unreachable,
    satisfying the graceful degradation requirement for balance queries.
    """
    trace_id = request.state.trace_id
    track("GET /accounts/{id}/balance")
    logger.info("Proxying balance request for account %s", account_id, extra={"trace_id": trace_id})
    return await account_client.get_balance(account_id, trace_id=trace_id)
