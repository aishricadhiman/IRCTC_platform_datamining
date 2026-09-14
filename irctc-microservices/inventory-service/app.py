import json
import os
import time
import uuid

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    Column,
    Integer
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
# Redis Configuration
# --------------------------------------------------
#
# Redis backs two independent features here:
#   1. A distributed lock per train_id so that concurrent
#      reserve/release calls hitting different replicas of
#      this service still serialize on the same train.
#   2. A short-lived cache for availability reads, since
#      that endpoint is read far more often than seats change.

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

CACHE_TTL_SECONDS = 5
LOCK_TTL_MS = 5000

# Only releases a lock if it still holds the token we set,
# so one request can never release a lock acquired by another
# after its own lock already expired.
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _lock_key(train_id: int) -> str:
    return f"inventory:lock:{train_id}"


def _cache_key(train_id: int) -> str:
    return f"inventory:availability:{train_id}"


class TrainLock:
    """Distributed lock over a train's inventory, backed by Redis SET NX PX."""

    def __init__(self, train_id: int):
        self.key = _lock_key(train_id)
        self.token = str(uuid.uuid4())

    def acquire(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:

            if redis_client.set(self.key, self.token, nx=True, px=LOCK_TTL_MS):
                return True

            time.sleep(0.05)

        return False

    def release(self):
        redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.token)

    def __enter__(self):
        if not self.acquire():
            raise HTTPException(
                status_code=503,
                detail="Could not acquire inventory lock, try again"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def invalidate_availability_cache(train_id: int):
    redis_client.delete(_cache_key(train_id))


# --------------------------------------------------
# Database Model
# --------------------------------------------------

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
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC Inventory Service")


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class InventoryCreate(BaseModel):
    train_id: int
    total_seats: int


# --------------------------------------------------
# Database Initialization
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:
            Base.metadata.create_all(bind=engine)

            print("Connected to Inventory PostgreSQL successfully.")
            print("Inventory table is ready.")

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


# --------------------------------------------------
# Create Inventory
# --------------------------------------------------

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


# --------------------------------------------------
# Check Availability
# --------------------------------------------------

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


# --------------------------------------------------
# Reserve One Seat
# --------------------------------------------------

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


# --------------------------------------------------
# Release One Seat
# --------------------------------------------------

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