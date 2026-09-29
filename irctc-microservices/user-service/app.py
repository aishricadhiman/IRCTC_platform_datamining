import os
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from auth import (
    create_access_token,
    get_current_user_id,
    hash_password,
    verify_password,
)


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
    password_hash = Column(String, nullable=False)


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


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


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
    """
    Preserved from before authentication existed: creates a user record
    with no login credential of its own. Since password_hash is required
    on the User model, a random, never-revealed placeholder is hashed and
    stored so this endpoint's existing request/response contract does not
    change - accounts created this way simply cannot authenticate via
    /auth/login until/unless a real password is set for them. New
    signups should use POST /auth/register instead.
    """

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
            email=user_data.email,
            password_hash=hash_password(uuid.uuid4().hex)
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
# Register (real, password-based signup)
# --------------------------------------------------

@app.post("/auth/register", response_model=TokenResponse)
def register(data: RegisterRequest):

    db = SessionLocal()

    try:

        existing_user = db.query(User).filter(
            User.email == data.email
        ).first()

        if existing_user:

            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )

        new_user = User(
            name=data.name,
            email=data.email,
            password_hash=hash_password(data.password)
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        token = create_access_token(new_user.id, new_user.email)

        return TokenResponse(access_token=token)

    finally:

        db.close()


# --------------------------------------------------
# Login
# --------------------------------------------------

@app.post("/auth/login", response_model=TokenResponse)
def login(data: LoginRequest):

    db = SessionLocal()

    try:

        user = db.query(User).filter(
            User.email == data.email
        ).first()

        if not user or not verify_password(data.password, user.password_hash):

            raise HTTPException(
                status_code=401,
                detail="Invalid email or password"
            )

        token = create_access_token(user.id, user.email)

        return TokenResponse(access_token=token)

    finally:

        db.close()


# --------------------------------------------------
# Current User (protected)
# --------------------------------------------------
#
# Registered ahead of GET /users/{user_id} so "/users/me" is matched as
# this literal route rather than falling into the {user_id}:int converter.

@app.get("/users/me")
def get_my_profile(current_user_id: int = Depends(get_current_user_id)):

    db = SessionLocal()

    try:

        user = db.query(User).filter(
            User.id == current_user_id
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