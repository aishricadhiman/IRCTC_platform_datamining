import os
import time

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


@app.on_event("startup")
def startup_event():

    initialize_database()


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

        return {
            "train_id": inventory.train_id,
            "total_seats": inventory.total_seats,
            "available_seats": inventory.available_seats
        }

    finally:

        db.close()


# --------------------------------------------------
# Reserve One Seat
# --------------------------------------------------

@app.post("/reserve/{train_id}")
def reserve(train_id: int):

    db = SessionLocal()

    try:

        # Lock this database row during the transaction.
        # This prevents two concurrent requests from
        # reserving the same last available seat.

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

        return {
            "train_id": inventory.train_id,
            "total_seats": inventory.total_seats,
            "available_seats": inventory.available_seats
        }

    finally:

        db.close()