import os
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker


app = FastAPI(title="Notification Service")


# --------------------------------------------------
# Database Configuration
# --------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5437/irctc_notifications"
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

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    booking_id = Column(
        Integer,
        nullable=True,
        index=True
    )

    message = Column(
        String,
        nullable=False
    )

    notification_type = Column(
        String,
        nullable=False
    )

    status = Column(
        String,
        nullable=False,
        default="SENT"
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class NotificationRequest(BaseModel):
    user_id: int
    booking_id: int | None = None
    message: str = Field(min_length=1)
    notification_type: str = "GENERAL"


# --------------------------------------------------
# Startup
# --------------------------------------------------

@app.on_event("startup")
def startup_event():
    max_retries = 15

    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            print("Notification database connected successfully.")
            return

        except Exception as error:
            print(
                f"Database connection failed. "
                f"Retry {attempt + 1}/{max_retries}: {error}"
            )
            time.sleep(2)

    raise RuntimeError(
        "Could not connect to the notification database."
    )


# --------------------------------------------------
# Health Check
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "service": "Notification Service",
        "status": "running"
    }


# --------------------------------------------------
# Create Notification
# --------------------------------------------------

@app.post("/notifications")
def create_notification(
    notification_request: NotificationRequest
):
    db = SessionLocal()

    try:
        notification = Notification(
            user_id=notification_request.user_id,
            booking_id=notification_request.booking_id,
            message=notification_request.message,
            notification_type=notification_request.notification_type,
            status="SENT"
        )

        db.add(notification)
        db.commit()
        db.refresh(notification)

        return {
            "message": "Notification created successfully",
            "notification": {
                "id": notification.id,
                "user_id": notification.user_id,
                "booking_id": notification.booking_id,
                "message": notification.message,
                "notification_type": notification.notification_type,
                "status": notification.status,
                "created_at": notification.created_at
            }
        }

    except Exception as error:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Notification creation failed: {str(error)}"
        )

    finally:
        db.close()


# --------------------------------------------------
# Get All Notifications
# --------------------------------------------------

@app.get("/notifications")
def get_all_notifications():
    db = SessionLocal()

    try:
        notifications = (
            db.query(Notification)
            .order_by(Notification.id.desc())
            .all()
        )

        return [
            {
                "id": notification.id,
                "user_id": notification.user_id,
                "booking_id": notification.booking_id,
                "message": notification.message,
                "notification_type": notification.notification_type,
                "status": notification.status,
                "created_at": notification.created_at
            }
            for notification in notifications
        ]

    finally:
        db.close()


# --------------------------------------------------
# Get Notifications by User ID
# --------------------------------------------------

@app.get("/notifications/user/{user_id}")
def get_user_notifications(user_id: int):
    db = SessionLocal()

    try:
        notifications = (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .order_by(Notification.id.desc())
            .all()
        )

        return [
            {
                "id": notification.id,
                "user_id": notification.user_id,
                "booking_id": notification.booking_id,
                "message": notification.message,
                "notification_type": notification.notification_type,
                "status": notification.status,
                "created_at": notification.created_at
            }
            for notification in notifications
        ]

    finally:
        db.close()


# --------------------------------------------------
# Get Notification by ID
# --------------------------------------------------

@app.get("/notifications/{notification_id}")
def get_notification(notification_id: int):
    db = SessionLocal()

    try:
        notification = (
            db.query(Notification)
            .filter(Notification.id == notification_id)
            .first()
        )

        if notification is None:
            raise HTTPException(
                status_code=404,
                detail="Notification not found"
            )

        return {
            "id": notification.id,
            "user_id": notification.user_id,
            "booking_id": notification.booking_id,
            "message": notification.message,
            "notification_type": notification.notification_type,
            "status": notification.status,
            "created_at": notification.created_at
        }

    finally:
        db.close()