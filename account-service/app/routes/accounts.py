import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone

from ..database import get_db
from ..models import Transaction, Account
from ..schemas import (
    TransactionRequest,
    TransactionResponse,
    BalanceResponse,
    AccountDetailsResponse,
)

router = APIRouter()
logger = logging.getLogger("account-service")


def _get_or_create_account(db: Session, account_id: str) -> Account:
    account = db.query(Account).filter(Account.account_id == account_id).first()
    if not account:
        account = Account(account_id=account_id)
        db.add(account)
        try:
            db.commit()
            db.refresh(account)
            logger.info("Account %s auto-created", account_id, extra={"trace_id": None})
        except SQLAlchemyError:
            db.rollback()
            # Concurrent creation — fetch the row the other writer committed
            account = db.query(Account).filter(Account.account_id == account_id).first()
    return account


def _calculate_balance(db: Session, account_id: str) -> tuple[float, int]:
    txs = db.query(Transaction).filter(Transaction.account_id == account_id).all()
    balance = sum(t.amount if t.type == "CREDIT" else -t.amount for t in txs)
    return round(balance, 2), len(txs)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@router.post("/accounts/{accountId}/transactions", response_model=TransactionResponse)
async def apply_transaction(
    accountId: str,
    transaction: TransactionRequest,
    db: Session = Depends(get_db),
    x_trace_id: Optional[str] = Header(None),
):
    extra = {"trace_id": x_trace_id}
    logger.info("Applying transaction %s to account %s", transaction.eventId, accountId, extra=extra)

    existing = db.query(Transaction).filter(Transaction.event_id == transaction.eventId).first()
    if existing:
        logger.info("Transaction %s already applied (idempotent)", transaction.eventId, extra=extra)
        return TransactionResponse(
            eventId=existing.event_id,
            accountId=accountId,
            type=existing.type,
            amount=existing.amount,
            currency=existing.currency,
            eventTimestamp=existing.event_timestamp,
            appliedAt=existing.applied_at,
            alreadyApplied=True,
        )

    _get_or_create_account(db, accountId)

    applied_at = datetime.now(timezone.utc)
    tx = Transaction(
        event_id=transaction.eventId,
        account_id=accountId,
        type=transaction.type,
        amount=transaction.amount,
        currency=transaction.currency,
        event_timestamp=_naive(transaction.eventTimestamp),
        applied_at=_naive(applied_at),
    )
    db.add(tx)
    try:
        db.commit()
        db.refresh(tx)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("DB write failed for transaction %s: %s", transaction.eventId, exc, extra=extra)
        raise HTTPException(status_code=503, detail="Database write failed — please retry")

    logger.info("Transaction %s applied successfully", transaction.eventId, extra=extra)

    return TransactionResponse(
        eventId=tx.event_id,
        accountId=accountId,
        type=tx.type,
        amount=tx.amount,
        currency=tx.currency,
        eventTimestamp=tx.event_timestamp,
        appliedAt=tx.applied_at,
        alreadyApplied=False,
    )


@router.get("/accounts/{accountId}/balance", response_model=BalanceResponse)
async def get_balance(
    accountId: str,
    db: Session = Depends(get_db),
    x_trace_id: Optional[str] = Header(None),
):
    extra = {"trace_id": x_trace_id}
    logger.info("Getting balance for account %s", accountId, extra=extra)

    account = db.query(Account).filter(Account.account_id == accountId).first()
    if not account:
        logger.warning("Balance requested for unknown account %s", accountId, extra=extra)
        raise HTTPException(status_code=404, detail=f"Account {accountId} not found")

    balance, count = _calculate_balance(db, accountId)
    logger.info("Balance for %s: %.2f (%d transactions)", accountId, balance, count, extra=extra)

    last_tx = (
        db.query(Transaction)
        .filter(Transaction.account_id == accountId)
        .order_by(Transaction.applied_at.desc())
        .first()
    )
    currency = last_tx.currency if last_tx else "USD"

    return BalanceResponse(accountId=accountId, balance=balance, currency=currency, transactionCount=count)


@router.get("/accounts/{accountId}", response_model=AccountDetailsResponse)
async def get_account(
    accountId: str,
    db: Session = Depends(get_db),
    x_trace_id: Optional[str] = Header(None),
):
    extra = {"trace_id": x_trace_id}
    logger.info("Getting account details for %s", accountId, extra=extra)

    account = db.query(Account).filter(Account.account_id == accountId).first()
    if not account:
        logger.warning("Account details requested for unknown account %s", accountId, extra=extra)
        raise HTTPException(status_code=404, detail=f"Account {accountId} not found")

    balance, _ = _calculate_balance(db, accountId)

    txs = (
        db.query(Transaction)
        .filter(Transaction.account_id == accountId)
        .order_by(Transaction.event_timestamp.desc())
        .limit(20)
        .all()
    )
    currency = txs[0].currency if txs else "USD"

    recent = [
        TransactionResponse(
            eventId=t.event_id,
            accountId=accountId,
            type=t.type,
            amount=t.amount,
            currency=t.currency,
            eventTimestamp=t.event_timestamp,
            appliedAt=t.applied_at,
            alreadyApplied=False,
        )
        for t in txs
    ]

    return AccountDetailsResponse(
        accountId=accountId,
        balance=balance,
        currency=currency,
        recentTransactions=recent,
        createdAt=account.created_at,
    )
