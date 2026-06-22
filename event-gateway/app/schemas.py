from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime


class EventCreate(BaseModel):
    eventId: str
    accountId: str
    type: str
    amount: float
    currency: str
    eventTimestamp: datetime
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("eventId")
    @classmethod
    def event_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("eventId must not be empty")
        return v

    @field_validator("accountId")
    @classmethod
    def account_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("accountId must not be empty")
        return v

    @field_validator("type")
    @classmethod
    def type_must_be_credit_or_debit(cls, v: str) -> str:
        if v not in ("CREDIT", "DEBIT"):
            raise ValueError("type must be 'CREDIT' or 'DEBIT'")
        return v

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v

    @field_validator("currency")
    @classmethod
    def currency_three_chars(cls, v: str) -> str:
        if not v or len(v) != 3:
            raise ValueError("currency must be a 3-letter code (e.g., USD)")
        return v.upper()


class EventResponse(BaseModel):
    eventId: str
    accountId: str
    type: str
    amount: float
    currency: str
    eventTimestamp: datetime
    metadata: Optional[Dict[str, Any]] = None
    receivedAt: datetime
    status: str


class EventListResponse(BaseModel):
    events: List[EventResponse]
    total: int
