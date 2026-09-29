import os

from sqlalchemy import (
    create_engine,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
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
# Booking
# --------------------------------------------------
#
# Phase 8 extends this table rather than replacing it. Every column added
# here is nullable, so the bookings written during Phases 1-5 - which had no
# concept of a class, a PNR, or more than one passenger - remain valid rows
# and keep working through the legacy single-passenger endpoint.
#
# passenger_name stays for exactly that reason: it is the only passenger
# information a legacy booking has. Multi-passenger bookings leave it NULL
# and use the passengers table instead.

class Booking(Base):

    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, nullable=False)

    train_id = Column(Integer, nullable=False)

    passenger_name = Column(String, nullable=True)      # legacy bookings only

    amount = Column(Float, nullable=False)

    # PENDING | AWAITING_PAYMENT | CONFIRMED | PARTIALLY_CONFIRMED
    # | WAITLISTED | PAYMENT_FAILED | FAILED | CANCELLED
    status = Column(String, nullable=False)

    # ---- Phase 8 additions (NULL on every legacy booking) ----

    class_type = Column(String, nullable=True)          # "SL", "3A", ...

    pnr = Column(String, nullable=True, index=True)

    # The idempotency key this booking's seats were allocated under in
    # Inventory Service. Also what cancellation and release are keyed on.
    allocation_ref = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=True)


# --------------------------------------------------
# Passenger
# --------------------------------------------------
#
# Booking Service owns passenger identity and each passenger's booking
# outcome. Inventory owns only the physical berth; the coach/seat/berth
# columns here are a copy of what Inventory allocated, kept so the ticket
# can be rendered without calling Inventory on every read.

class Passenger(Base):

    __tablename__ = "passengers"

    id = Column(Integer, primary_key=True, index=True)

    booking_id = Column(
        Integer,
        ForeignKey("bookings.id"),
        nullable=False,
        index=True
    )

    # Stable handle used when talking to Inventory about this passenger.
    passenger_ref = Column(String, nullable=False)

    # Which Inventory allocation currently holds this passenger's berth.
    # Normally the booking's own allocation_ref, but a waitlisted passenger
    # promoted later gets its own ref: Inventory replays an allocation_ref it
    # has already seen, so reusing the booking's ref would return the
    # original result instead of allocating the newly freed berth.
    allocation_ref = Column(String, nullable=True, index=True)

    name = Column(String, nullable=False)

    age = Column(Integer, nullable=True)

    gender = Column(String, nullable=True)

    berth_preference = Column(String, nullable=False, default="NONE")

    # CNF | RAC | WL | CANCELLED
    status = Column(String, nullable=False, default="WL")

    coach_name = Column(String, nullable=True)

    seat_number = Column(Integer, nullable=True)

    berth_type = Column(String, nullable=True)


# --------------------------------------------------
# Waitlist Queue
# --------------------------------------------------
#
# The waiting list lives here, not in Inventory: a waitlisted passenger
# occupies no berth, so there is nothing physical for Inventory to own. When
# a cancellation frees RAC capacity, Inventory reports that capacity and this
# service decides - from this queue - who gets it.

class WaitlistQueue(Base):

    __tablename__ = "waitlist_queue"

    id = Column(Integer, primary_key=True, index=True)

    booking_id = Column(
        Integer,
        ForeignKey("bookings.id"),
        nullable=False,
        index=True
    )

    passenger_id = Column(
        Integer,
        ForeignKey("passengers.id"),
        nullable=False,
        index=True
    )

    train_id = Column(Integer, nullable=False, index=True)

    class_type = Column(String, nullable=False)

    # 1-based position within (train_id, class_type), resequenced whenever
    # somebody leaves the queue.
    priority_number = Column(Integer, nullable=False)

    # WAITING | UPGRADED | CANCELLED
    status = Column(String, nullable=False, default="WAITING")

    created_at = Column(DateTime, server_default=func.now(), nullable=True)
