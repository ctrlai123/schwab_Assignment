from pydantic import BaseModel
from typing import List
from datetime import datetime


class TransactionRequest(BaseModel):
    eventId: str
    type: str
    amount: float
    currency: str
    eventTimestamp: datetime


class TransactionResponse(BaseModel):
    eventId: str
    accountId: str
    type: str
    amount: float
    currency: str
    eventTimestamp: datetime
    appliedAt: datetime
    alreadyApplied: bool = False


class BalanceResponse(BaseModel):
    accountId: str
    balance: float
    currency: str
    transactionCount: int


class AccountDetailsResponse(BaseModel):
    accountId: str
    balance: float
    currency: str
    recentTransactions: List[TransactionResponse]
    createdAt: datetime
