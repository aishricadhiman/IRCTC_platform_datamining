import os

from sqlalchemy import (
    create_engine,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker


# --------------------------------------------------
# Database Configuration
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/irctc_inventory"
)

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# --------------------------------------------------
# Legacy Model (Phases 1-5) - UNCHANGED
# --------------------------------------------------
#
# The original integer-counter inventory. Phase 6 leaves this table and the
# endpoints built on it exactly as they were: Booking Service still uses
# /reserve/{train_id} and /release/{train_id} against this row until the
# Phase 8 cutover. The per-seat tables below are a parallel model that is
# not yet in service.

class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)

    train_id = Column(
        Integer,
        unique=True,
        nullable=False,
        index=True
    )

    total_seats = Column(Integer, nullable=False)

    available_seats = Column(Integer, nullable=False)


# --------------------------------------------------
# Phase 6: Per-Seat Physical Inventory
# --------------------------------------------------
#
# Inventory Service owns PHYSICAL berth occupancy only: which coaches and
# seats exist, what berth each one is, and whether it is currently taken.
# It does NOT own passenger records, PNRs, or the waiting list - a WL
# outcome is returned to the caller but nothing is persisted for it here
# (Booking Service owns the WL queue from Phase 8).
#
# train_id is Train Service's integer trains.id. Inventory deliberately
# does not know or join on train_number.

class Coach(Base):
    __tablename__ = "coaches"

    id = Column(Integer, primary_key=True, index=True)

    train_id = Column(Integer, nullable=False, index=True)

    name = Column(String, nullable=False)          # e.g. "S1"

    class_type = Column(String, nullable=False)    # e.g. "SL", "3A"

    total_seats = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("train_id", "name", name="uq_coach_train_name"),
    )


class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, index=True)

    coach_id = Column(
        Integer,
        ForeignKey("coaches.id"),
        nullable=False,
        index=True
    )

    seat_number = Column(Integer, nullable=False)

    # LOWER | MIDDLE | UPPER | SIDE_LOWER | SIDE_UPPER
    berth_type = Column(String, nullable=False)

    # VACANT   - free, allocatable
    # BOOKED   - held by a CNF allocation
    # RAC_ONE  - SIDE_LOWER berth with one RAC passenger (room for one more)
    # RAC_FULL - SIDE_LOWER berth shared by two RAC passengers
    # BLOCKED  - not allocatable; see seed.py for the historical-capacity note
    status = Column(String, nullable=False, default="VACANT")

    __table_args__ = (
        UniqueConstraint("coach_id", "seat_number", name="uq_seat_coach_number"),
    )


class SeatAllocation(Base):
    """
    One row per passenger-seat assignment.

    This is deliberately NOT a booking_id column on Seat: a RAC_FULL berth is
    shared by two passengers who may belong to two different bookings, which a
    single foreign key on the seat cannot represent. Keeping assignments in
    their own table also gives release/ a real target and makes allocation
    idempotent through allocation_ref.
    """

    __tablename__ = "seat_allocations"

    id = Column(Integer, primary_key=True, index=True)

    seat_id = Column(
        Integer,
        ForeignKey("seats.id"),
        nullable=False,
        index=True
    )

    # Caller-supplied idempotency key for one allocation request (which may
    # cover several passengers). Replaying the same ref returns the original
    # result instead of allocating again.
    allocation_ref = Column(String, nullable=False, index=True)

    booking_id = Column(Integer, nullable=True, index=True)

    # Caller's own identifier for the passenger within the request. Inventory
    # stores it only to echo outcomes back; it holds no passenger details.
    passenger_ref = Column(String, nullable=True)

    outcome = Column(String, nullable=False)       # CNF | RAC

    coach_name = Column(String, nullable=False)

    seat_number = Column(Integer, nullable=False)

    berth_type = Column(String, nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # NULL while the allocation is live; set when released. Rows are never
    # deleted, so a released allocation stays auditable.
    released_at = Column(DateTime, nullable=True)
