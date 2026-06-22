from sqlalchemy import Column, String, Float, DateTime
from datetime import datetime, timezone
from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    event_id = Column(String, primary_key=True, index=True)
    account_id = Column(String, index=True, nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    event_timestamp = Column(DateTime, nullable=False)
    applied_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Account(Base):
    __tablename__ = "accounts"

    account_id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
