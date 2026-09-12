import os
import time

import httpx

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float
)

from sqlalchemy.orm import declarative_base, sessionmaker


# --------------------------------------------------
# Database Configuration
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/irctc_bookings"
)

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# --------------------------------------------------
# Other Microservice URLs
# --------------------------------------------------

INVENTORY_URL = os.getenv(
    "INVENTORY_URL",
    "http://localhost:8003"
)

PAYMENT_URL = os.getenv(
    "PAYMENT_URL",
    "http://localhost:8005"
)

NOTIFICATION_URL = os.getenv(
    "NOTIFICATION_URL",
    "http://localhost:8006"
)


# --------------------------------------------------
# Database Model
# --------------------------------------------------

class Booking(Base):

    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, nullable=False)

    train_id = Column(Integer, nullable=False)

    passenger_name = Column(String, nullable=False)

    amount = Column(Float, nullable=False)

    status = Column(String, nullable=False)


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC Booking Service")


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class BookingCreate(BaseModel):

    user_id: int

    train_id: int

    passenger_name: str

    amount: float


# --------------------------------------------------
# Database Initialization
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:

            Base.metadata.create_all(bind=engine)

            print("Connected to Booking PostgreSQL successfully.")
            print("Bookings table is ready.")

            return

        except Exception as error:

            print(f"Database connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to Booking PostgreSQL.")


@app.on_event("startup")
def startup_event():

    initialize_database()


# --------------------------------------------------
# Create Booking
# --------------------------------------------------

@app.post("/bookings")
async def create_booking(data: BookingCreate):

    db = SessionLocal()

    booking = None

    try:

        # ------------------------------------------
        # 1. Create a pending booking
        # ------------------------------------------

        booking = Booking(
            user_id=data.user_id,
            train_id=data.train_id,
            passenger_name=data.passenger_name,
            amount=data.amount,
            status="PENDING"
        )

        db.add(booking)

        db.commit()

        db.refresh(booking)

        booking_id = booking.id

        # ------------------------------------------
        # 2. Reserve a seat
        # ------------------------------------------

        async with httpx.AsyncClient() as client:

            reserve_response = await client.post(
                f"{INVENTORY_URL}/reserve/{data.train_id}"
            )

            if reserve_response.status_code != 200:

                booking.status = "FAILED"

                db.commit()

                raise HTTPException(
                    status_code=400,
                    detail="Seat reservation failed"
                )

            # --------------------------------------
            # 3. Process payment
            # --------------------------------------

            payment_response = await client.post(
                f"{PAYMENT_URL}/payments",
                json={
                    "booking_id": booking_id,
                    "amount": data.amount,
                    "idempotency_key": f"booking-{booking.id}-payment"
                }
            )

            if payment_response.status_code != 200:

                # Release the reserved seat
                await client.post(
                    f"{INVENTORY_URL}/release/{data.train_id}"
                )

                booking.status = "PAYMENT_FAILED"

                db.commit()

                raise HTTPException(
                    status_code=400,
                    detail="Payment failed"
                )

            payment_data = payment_response.json()

            # --------------------------------------
            # 4. Confirm booking
            # --------------------------------------

            booking.status = "CONFIRMED"

            db.commit()

            db.refresh(booking)

            # --------------------------------------
            # 5. Send notification
            # --------------------------------------

            notification_response = client.post(
                f"{NOTIFICATION_URL}/notifications",
                json={
                    "user_id": booking.user_id,
                    "booking_id": booking.id,
                    "message": (
                        f"Your booking for train {booking.train_id} "
                        f"has been confirmed."
                    ),
                    "notification_type": "BOOKING_CONFIRMED"
                }
            )

            return {
                "message": "Booking confirmed",
                "booking": {
                    "id": booking.id,
                    "user_id": booking.user_id,
                    "train_id": booking.train_id,
                    "passenger_name": booking.passenger_name,
                    "amount": booking.amount,
                    "status": booking.status
                },
                "payment": payment_data
            }

    except HTTPException:

        raise

    except Exception as error:

        print("Booking error:", error)

        if booking:

            booking.status = "FAILED"

            db.commit()

        raise HTTPException(
            status_code=500,
            detail="Booking could not be completed"
        )

    finally:

        db.close()


# --------------------------------------------------
# Get All Bookings
# --------------------------------------------------

@app.get("/bookings")
def get_bookings():

    db = SessionLocal()

    try:

        bookings = db.query(Booking).all()

        return [

            {
                "id": booking.id,
                "user_id": booking.user_id,
                "train_id": booking.train_id,
                "passenger_name": booking.passenger_name,
                "amount": booking.amount,
                "status": booking.status
            }

            for booking in bookings

        ]

    finally:

        db.close()


# --------------------------------------------------
# Get Booking by ID
# --------------------------------------------------

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: int):

    db = SessionLocal()

    try:

        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:

            raise HTTPException(
                status_code=404,
                detail="Booking not found"
            )

        return {

            "id": booking.id,
            "user_id": booking.user_id,
            "train_id": booking.train_id,
            "passenger_name": booking.passenger_name,
            "amount": booking.amount,
            "status": booking.status

        }

    finally:

        db.close()