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
    "postgresql://postgres:postgres@localhost:5432/irctc_users"
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

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC User Service")


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class UserCreate(BaseModel):
    name: str
    email: str


# --------------------------------------------------
# Database Initialization
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:
            Base.metadata.create_all(bind=engine)

            print("Connected to PostgreSQL successfully.")
            print("Users table is ready.")

            return

        except Exception as error:

            print(f"Database connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to PostgreSQL.")


@app.on_event("startup")
def startup_event():

    initialize_database()


# --------------------------------------------------
# Create User
# --------------------------------------------------

@app.post("/users")
def create_user(user_data: UserCreate):

    db = SessionLocal()

    try:

        existing_user = db.query(User).filter(
            User.email == user_data.email
        ).first()

        if existing_user:

            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )

        new_user = User(
            name=user_data.name,
            email=user_data.email
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return {
            "id": new_user.id,
            "name": new_user.name,
            "email": new_user.email
        }

    finally:

        db.close()


# --------------------------------------------------
# Get All Users
# --------------------------------------------------

@app.get("/users")
def get_users():

    db = SessionLocal()

    try:

        users = db.query(User).all()

        return [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email
            }
            for user in users
        ]

    finally:

        db.close()


# --------------------------------------------------
# Get User by ID
# --------------------------------------------------

@app.get("/users/{user_id}")
def get_user(user_id: int):

    db = SessionLocal()

    try:

        user = db.query(User).filter(
            User.id == user_id
        ).first()

        if not user:

            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        return {
            "id": user.id,
            "name": user.name,
            "email": user.email
        }

    finally:

        db.close()