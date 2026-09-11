import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

app = FastAPI(title="Payment Service")


# --------------------------------------------------
# Database Configuration
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5436/irctc_payments"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# --------------------------------------------------
# Database Model
# --------------------------------------------------

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)

    payment_id = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    booking_id = Column(
        Integer,
        nullable=False
    )

    amount = Column(
        Float,
        nullable=False
    )

    status = Column(
        String,
        nullable=False
    )

    # Unique key supplied by the client or Booking Service
    idempotency_key = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class PaymentRequest(BaseModel):
    booking_id: int
    amount: float = Field(gt=0)
    idempotency_key: str = Field(
        min_length=1,
        max_length=255
    )


# --------------------------------------------------
# Startup
# --------------------------------------------------

@app.on_event("startup")
def startup_event():
    max_retries = 15

    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            print("Payment database connected successfully.")
            return

        except Exception as error:
            print(
                f"Database connection failed. "
                f"Retry {attempt + 1}/{max_retries}: {error}"
            )
            time.sleep(2)

    raise RuntimeError(
        "Could not connect to the payment database."
    )


# --------------------------------------------------
# Health Check
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "service": "Payment Service",
        "status": "running"
    }


# --------------------------------------------------
# Create Payment with Idempotency
# --------------------------------------------------

@app.post("/payments")
def create_payment(payment_request: PaymentRequest):
    db = SessionLocal()

    try:
        # 1. Check if this idempotency key was already processed
        existing_payment = (
            db.query(Payment)
            .filter(
                Payment.idempotency_key
                == payment_request.idempotency_key
            )
            .first()
        )

        if existing_payment is not None:
            return {
                "message": "Payment already processed",
                "payment_id": existing_payment.payment_id,
                "booking_id": existing_payment.booking_id,
                "amount": existing_payment.amount,
                "status": existing_payment.status,
                "idempotent": True
            }

        # 2. Create a new payment
        generated_payment_id = str(uuid.uuid4())

        payment = Payment(
            payment_id=generated_payment_id,
            booking_id=payment_request.booking_id,
            amount=payment_request.amount,
            status="SUCCESS",
            idempotency_key=payment_request.idempotency_key
        )

        db.add(payment)
        db.commit()
        db.refresh(payment)

        return {
            "message": "Payment successful",
            "payment_id": payment.payment_id,
            "booking_id": payment.booking_id,
            "amount": payment.amount,
            "status": payment.status,
            "idempotent": False
        }

    except Exception as error:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Payment processing failed: {str(error)}"
        )

    finally:
        db.close()


# --------------------------------------------------
# Get Payment by Payment ID
# --------------------------------------------------

@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    db = SessionLocal()

    try:
        payment = (
            db.query(Payment)
            .filter(Payment.payment_id == payment_id)
            .first()
        )

        if payment is None:
            raise HTTPException(
                status_code=404,
                detail="Payment not found"
            )

        return {
            "id": payment.id,
            "payment_id": payment.payment_id,
            "booking_id": payment.booking_id,
            "amount": payment.amount,
            "status": payment.status,
            "idempotency_key": payment.idempotency_key
        }

    finally:
        db.close()


# --------------------------------------------------
# Get All Payments
# --------------------------------------------------

@app.get("/payments")
def get_all_payments():
    db = SessionLocal()

    try:
        payments = db.query(Payment).all()

        return [
            {
                "id": payment.id,
                "payment_id": payment.payment_id,
                "booking_id": payment.booking_id,
                "amount": payment.amount,
                "status": payment.status,
                "idempotency_key": payment.idempotency_key
            }
            for payment in payments
        ]

    finally:
        db.close()