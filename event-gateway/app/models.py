from sqlalchemy import Column, String, Float, DateTime, JSON
from datetime import datetime, timezone
from .database import Base


class Event(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True, index=True)
    account_id = Column(String, index=True, nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    event_timestamp = Column(DateTime, nullable=False)
    metadata_ = Column("metadata", JSON, nullable=True)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    status = Column(String, default="pending")
