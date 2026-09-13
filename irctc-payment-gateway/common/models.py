"""
SQLAlchemy ORM models - the Payment-Service-relevant tables from
database/schema.sql in the design document (Users/Trains/Seats/Bookings
removed since only Payment Service remains in this trimmed copy).
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, JSON

from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"
    transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    txn_reference = Column(String(64), unique=True, nullable=False)
    booking_id = Column(Integer, nullable=False)  # no FK - Booking Service owns bookings in the full system
    user_id = Column(Integer, nullable=False)
    idempotency_key = Column(String(64), unique=True, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(20), nullable=False)
    pg_provider = Column(String(30), nullable=False, default="MOCK_PG")
    pg_order_id = Column(String(64), nullable=True)    # set immediately (real PG: Razorpay order id)
    pg_payment_id = Column(String(64), nullable=True)  # set only once checkout completes
    status = Column(String(20), nullable=False, default="INITIATED")
    failure_reason = Column(String(255), nullable=True)
    version = Column(Integer, nullable=False, default=0)  # optimistic locking for the state machine
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    refund_id = Column(Integer, primary_key=True, autoincrement=True)
    refund_reference = Column(String(64), unique=True, nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.transaction_id"), nullable=False)
    booking_id = Column(Integer, nullable=False)
    idempotency_key = Column(String(64), unique=True, nullable=False)
    refund_amount = Column(Numeric(10, 2), nullable=False)
    cancellation_charge = Column(Numeric(10, 2), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="INITIATED")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentAuditLog(Base):
    __tablename__ = "payment_audit_log"
    audit_id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=True)
    booking_id = Column(Integer, nullable=True)
    event_type = Column(String(50), nullable=False)
    old_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=True)
    source = Column(String(30), nullable=False)  # SYSTEM | PG_CALLBACK | RECONCILIATION_JOB | USER
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

