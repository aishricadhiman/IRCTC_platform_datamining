import json
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from allocation import (
    allocate_seats,
    find_existing_allocation,
    release_allocation,
)
from locks import (
    CACHE_TTL_SECONDS,
    TrainLock,
    _cache_key,
    _seat_cache_key,
    invalidate_availability_cache,
    invalidate_seat_availability_cache,
    redis_client,
)
from models import Base, Coach, Inventory, Seat, SeatAllocation, SessionLocal, engine
from promotion import cancel_allocations, count_rac_capacity
from seed import seed_inventory


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC Inventory Service")


# --------------------------------------------------
# Request Schemas
# --------------------------------------------------

class InventoryCreate(BaseModel):
    train_id: int
    total_seats: int


class PassengerRequest(BaseModel):
    # The caller's own handle for this passenger. Inventory stores it only to
    # echo outcomes back - no passenger details are kept here.
    passenger_ref: str
    age: Optional[int] = None
    gender: Optional[str] = None
    berth_preference: str = "NONE"


class AllocateRequest(BaseModel):
    class_type: str
    allocation_ref: str
    booking_id: Optional[int] = None
    passengers: List[PassengerRequest]

    # TEST HOOK ONLY - raises after seats have been modified but before the
    # commit, to prove the transaction rolls back cleanly. Defaults to False
    # and is a no-op for real traffic. Mirrors the force_outcome /
    # simulate_confirm_failure hooks already used elsewhere in this project.
    simulate_failure: bool = False


class ReleaseRequest(BaseModel):
    allocation_ref: str


class CancelRequest(BaseModel):
    class_type: str
    allocation_ref: str
    # Omit to cancel every passenger on this allocation; supply a subset to
    # cancel only those passengers.
    passenger_refs: Optional[List[str]] = None


# --------------------------------------------------
# Startup
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:
            # Additive: creates the new per-seat tables and leaves the legacy
            # inventory table exactly as it is.
            Base.metadata.create_all(bind=engine)

            print("Connected to Inventory PostgreSQL successfully.")
            print("Inventory tables are ready.")

            return

        except Exception as error:

            print(f"Database connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to Inventory PostgreSQL.")


def initialize_redis():

    for attempt in range(10):

        try:
            redis_client.ping()

            print("Connected to Redis successfully.")

            return

        except Exception as error:

            print(f"Redis connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to Redis.")


@app.on_event("startup")
def startup_event():

    initialize_database()
    initialize_redis()

    # Idempotent - returns immediately if the coach is already seeded.
    seed_inventory()


# ==================================================
# LEGACY COUNTER API (Phases 1-5) - BEHAVIOUR UNCHANGED
# ==================================================
#
# Booking Service still drives these until the Phase 8 cutover. They operate
# solely on the legacy `inventory` counter row and never touch the per-seat
# tables below.


@app.post("/inventory")
def create_inventory(data: InventoryCreate):

    if data.total_seats <= 0:

        raise HTTPException(
            status_code=400,
            detail="Total seats must be greater than zero"
        )

    db = SessionLocal()

    try:

        existing_inventory = db.query(Inventory).filter(
            Inventory.train_id == data.train_id
        ).first()

        if existing_inventory:

            raise HTTPException(
                status_code=400,
                detail="Inventory already exists for this train"
            )

        new_inventory = Inventory(
            train_id=data.train_id,
            total_seats=data.total_seats,
            available_seats=data.total_seats
        )

        db.add(new_inventory)
        db.commit()
        db.refresh(new_inventory)

        invalidate_availability_cache(new_inventory.train_id)

        return {
            "train_id": new_inventory.train_id,
            "total_seats": new_inventory.total_seats,
            "available_seats": new_inventory.available_seats
        }

    finally:

        db.close()


@app.get("/availability/{train_id}")
def availability(train_id: int):

    cached = redis_client.get(_cache_key(train_id))

    if cached is not None:
        return json.loads(cached)

    db = SessionLocal()

    try:

        inventory = db.query(Inventory).filter(
            Inventory.train_id == train_id
        ).first()

        if not inventory:

            raise HTTPException(
                status_code=404,
                detail="Inventory not found"
            )

        result = {
            "train_id": inventory.train_id,
            "total_seats": inventory.total_seats,
            "available_seats": inventory.available_seats
        }

        redis_client.setex(
            _cache_key(train_id),
            CACHE_TTL_SECONDS,
            json.dumps(result)
        )

        return result

    finally:

        db.close()


@app.post("/reserve/{train_id}")
def reserve(train_id: int):

    with TrainLock(train_id):

        db = SessionLocal()

        try:

            # The Redis lock above serializes requests for this
            # train across every replica of this service; the
            # row lock below still guards against any writer
            # that bypasses the Redis lock (e.g. a direct DB client).

            inventory = db.query(Inventory).filter(
                Inventory.train_id == train_id
            ).with_for_update().first()

            if not inventory:

                raise HTTPException(
                    status_code=404,
                    detail="Inventory not found"
                )

            if inventory.available_seats <= 0:

                raise HTTPException(
                    status_code=409,
                    detail="No seats available"
                )

            inventory.available_seats -= 1

            db.commit()
            db.refresh(inventory)

            invalidate_availability_cache(train_id)

            return {
                "reserved": True,
                "train_id": inventory.train_id,
                "total_seats": inventory.total_seats,
                "available_seats": inventory.available_seats
            }

        finally:

            db.close()


@app.post("/release/{train_id}")
def release(train_id: int):

    with TrainLock(train_id):

        db = SessionLocal()

        try:

            inventory = db.query(Inventory).filter(
                Inventory.train_id == train_id
            ).with_for_update().first()

            if not inventory:

                raise HTTPException(
                    status_code=404,
                    detail="Inventory not found"
                )

            if inventory.available_seats < inventory.total_seats:

                inventory.available_seats += 1

            db.commit()
            db.refresh(inventory)

            invalidate_availability_cache(train_id)

            return {
                "train_id": inventory.train_id,
                "total_seats": inventory.total_seats,
                "available_seats": inventory.available_seats
            }

        finally:

            db.close()


# ==================================================
# PHASE 6 PER-SEAT API
# ==================================================
#
# Reads live under /inventory/... and are reachable through the API Gateway.
# Mutations live under /internal/... , which the Gateway does not route, so
# a browser cannot allocate or release berths directly - the same isolation
# pattern already used by Booking Service's /internal/payment-events.


@app.get("/inventory/{train_id}/seats")
def get_seats(train_id: int, class_type: str):

    db = SessionLocal()

    try:

        coaches = db.query(Coach).filter(
            Coach.train_id == train_id,
            Coach.class_type == class_type
        ).order_by(Coach.id.asc()).all()

        if not coaches:

            raise HTTPException(
                status_code=404,
                detail=f"No coaches found for train {train_id} class {class_type}"
            )

        payload = []

        for coach in coaches:

            seats = db.query(Seat).filter(
                Seat.coach_id == coach.id
            ).order_by(Seat.seat_number.asc()).all()

            payload.append({
                "coach_name": coach.name,
                "class_type": coach.class_type,
                "total_seats": coach.total_seats,
                "seats": [
                    {
                        "seat_number": seat.seat_number,
                        "berth_type": seat.berth_type,
                        "status": seat.status,
                    }
                    for seat in seats
                ]
            })

        return {
            "train_id": train_id,
            "class_type": class_type,
            "coaches": payload
        }

    finally:

        db.close()


@app.get("/inventory/{train_id}/availability")
def seat_availability(train_id: int, class_type: str):

    cached = redis_client.get(_seat_cache_key(train_id, class_type))

    if cached is not None:
        return json.loads(cached)

    db = SessionLocal()

    try:

        coaches = db.query(Coach).filter(
            Coach.train_id == train_id,
            Coach.class_type == class_type
        ).all()

        if not coaches:

            raise HTTPException(
                status_code=404,
                detail=f"No coaches found for train {train_id} class {class_type}"
            )

        coach_ids = [coach.id for coach in coaches]

        seats = db.query(Seat).filter(Seat.coach_id.in_(coach_ids)).all()

        total = len(seats)
        blocked = sum(1 for seat in seats if seat.status == "BLOCKED")

        cnf_available = sum(
            1 for seat in seats
            if seat.status == "VACANT" and seat.berth_type != "SIDE_LOWER"
        )

        cnf_booked = sum(1 for seat in seats if seat.status == "BOOKED")

        rac_one = sum(1 for seat in seats if seat.status == "RAC_ONE")
        rac_full = sum(1 for seat in seats if seat.status == "RAC_FULL")

        rac_vacant_berths = sum(
            1 for seat in seats
            if seat.status == "VACANT" and seat.berth_type == "SIDE_LOWER"
        )

        result = {
            "train_id": train_id,
            "class_type": class_type,
            "total_seats": total,
            # Historical consumed capacity whose seat identity is unknown -
            # see seed.py. Never allocatable.
            "blocked_seats": blocked,
            "cnf_available": cnf_available,
            "cnf_booked": cnf_booked,
            # Each SIDE_LOWER berth holds two RAC passengers.
            "rac_slots_available": (rac_vacant_berths * 2) + rac_one,
            "rac_slots_used": rac_one + (rac_full * 2),
        }

        redis_client.setex(
            _seat_cache_key(train_id, class_type),
            CACHE_TTL_SECONDS,
            json.dumps(result)
        )

        return result

    finally:

        db.close()


@app.post("/internal/inventory/{train_id}/allocate")
def allocate(train_id: int, request: AllocateRequest):

    if not request.passengers:

        raise HTTPException(
            status_code=400,
            detail="At least one passenger is required"
        )

    with TrainLock(train_id, request.class_type):

        db = SessionLocal()

        try:

            replay = find_existing_allocation(
                db, request.allocation_ref, request.passengers
            )

            if replay is not None:
                return replay

            result = allocate_seats(
                db=db,
                train_id=train_id,
                class_type=request.class_type,
                allocation_ref=request.allocation_ref,
                booking_id=request.booking_id,
                passengers=request.passengers,
            )

            if request.simulate_failure:
                raise RuntimeError(
                    "Simulated allocation failure before commit (test hook)"
                )

            db.commit()

            invalidate_seat_availability_cache(train_id, request.class_type)

            return result

        except ValueError as error:

            db.rollback()

            raise HTTPException(status_code=404, detail=str(error))

        except HTTPException:

            db.rollback()
            raise

        except Exception as error:

            db.rollback()

            raise HTTPException(
                status_code=500,
                detail=f"Allocation failed: {error}"
            )

        finally:

            db.close()


@app.post("/internal/inventory/{train_id}/release")
def release_seats(train_id: int, request: ReleaseRequest):

    # Work out which class this allocation belongs to so the right lock is
    # taken. A ref with nothing outstanding needs no lock at all - returning
    # an empty result is what makes a duplicate release harmless.
    db = SessionLocal()

    try:

        live = db.query(SeatAllocation, Coach.class_type).join(
            Seat, Seat.id == SeatAllocation.seat_id
        ).join(
            Coach, Coach.id == Seat.coach_id
        ).filter(
            SeatAllocation.allocation_ref == request.allocation_ref,
            SeatAllocation.released_at.is_(None)
        ).first()

        class_type = live[1] if live else None

    finally:

        db.close()

    if class_type is None:

        return {
            "allocation_ref": request.allocation_ref,
            "released": []
        }

    with TrainLock(train_id, class_type):

        db = SessionLocal()

        try:

            released = release_allocation(db, request.allocation_ref)

            db.commit()

            invalidate_seat_availability_cache(train_id, class_type)

            return {
                "allocation_ref": request.allocation_ref,
                "released": released
            }

        except HTTPException:

            db.rollback()
            raise

        except Exception as error:

            db.rollback()

            raise HTTPException(
                status_code=500,
                detail=f"Release failed: {error}"
            )

        finally:

            db.close()


@app.post("/internal/inventory/{train_id}/cancel")
def cancel_seats(train_id: int, request: CancelRequest):
    """
    Cancels booked berths and runs the RAC -> CNF promotion each freed
    confirmed berth triggers.

    Waiting-list promotion is deliberately NOT done here: Inventory has no
    queue. The response reports how much RAC capacity is free afterwards so
    Booking Service, which owns the waiting list, can decide who fills it and
    call allocate() for them.
    """

    with TrainLock(train_id, request.class_type):

        db = SessionLocal()

        try:

            cancelled, promotions = cancel_allocations(
                db=db,
                train_id=train_id,
                class_type=request.class_type,
                allocation_ref=request.allocation_ref,
                passenger_refs=request.passenger_refs,
            )

            db.commit()

            invalidate_seat_availability_cache(train_id, request.class_type)

            # Re-read post-commit so the figure reported back is the settled
            # state rather than the mid-transaction one.
            rac_slots_available = 0

            coaches = db.query(Coach).filter(
                Coach.train_id == train_id,
                Coach.class_type == request.class_type
            ).all()

            if coaches:
                seats = db.query(Seat).filter(
                    Seat.coach_id.in_([coach.id for coach in coaches])
                ).all()
                rac_slots_available = count_rac_capacity(
                    {seat.id: seat for seat in seats}
                )

            return {
                "allocation_ref": request.allocation_ref,
                "cancelled": cancelled,
                "promotions": promotions,
                "rac_slots_available": rac_slots_available,
            }

        except ValueError as error:

            db.rollback()

            raise HTTPException(status_code=404, detail=str(error))

        except HTTPException:

            db.rollback()
            raise

        except Exception as error:

            db.rollback()

            raise HTTPException(
                status_code=500,
                detail=f"Cancellation failed: {error}"
            )

        finally:

            db.close()
