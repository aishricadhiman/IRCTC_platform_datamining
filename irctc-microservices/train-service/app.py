import os
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker


# --------------------------------------------------
# Database Configuration
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/irctc_trains"
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

class Train(Base):
    __tablename__ = "trains"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    source = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    total_seats = Column(Integer, nullable=False)


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC Train Service")


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class TrainCreate(BaseModel):
    number: str
    name: str
    source: str
    destination: str
    total_seats: int


# --------------------------------------------------
# Database Initialization
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:
            Base.metadata.create_all(bind=engine)

            print("Connected to Train PostgreSQL successfully.")
            print("Trains table is ready.")

            return

        except Exception as error:

            print(f"Database connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to Train PostgreSQL.")


@app.on_event("startup")
def startup_event():

    initialize_database()


# --------------------------------------------------
# Create Train
# --------------------------------------------------

@app.post("/trains")
def create_train(train_data: TrainCreate):

    db = SessionLocal()

    try:

        existing_train = db.query(Train).filter(
            Train.number == train_data.number
        ).first()

        if existing_train:

            raise HTTPException(
                status_code=400,
                detail="Train number already exists"
            )

        new_train = Train(
            number=train_data.number,
            name=train_data.name,
            source=train_data.source,
            destination=train_data.destination,
            total_seats=train_data.total_seats
        )

        db.add(new_train)
        db.commit()
        db.refresh(new_train)

        return {
            "id": new_train.id,
            "number": new_train.number,
            "name": new_train.name,
            "source": new_train.source,
            "destination": new_train.destination,
            "total_seats": new_train.total_seats
        }

    finally:

        db.close()


# --------------------------------------------------
# Get All Trains / Search Trains
# --------------------------------------------------

@app.get("/trains")
def get_trains(
    source: str = None,
    destination: str = None
):

    db = SessionLocal()

    try:

        query = db.query(Train)

        if source:
            query = query.filter(Train.source == source)

        if destination:
            query = query.filter(
                Train.destination == destination
            )

        trains = query.all()

        return [
            {
                "id": train.id,
                "number": train.number,
                "name": train.name,
                "source": train.source,
                "destination": train.destination,
                "total_seats": train.total_seats
            }
            for train in trains
        ]

    finally:

        db.close()


# --------------------------------------------------
# Get Train by ID
# --------------------------------------------------

@app.get("/trains/{train_id}")
def get_train(train_id: int):

    db = SessionLocal()

    try:

        train = db.query(Train).filter(
            Train.id == train_id
        ).first()

        if not train:

            raise HTTPException(
                status_code=404,
                detail="Train not found"
            )

        return {
            "id": train.id,
            "number": train.number,
            "name": train.name,
            "source": train.source,
            "destination": train.destination,
            "total_seats": train.total_seats
        }

    finally:

        db.close()