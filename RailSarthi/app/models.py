from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Date, Boolean
from sqlalchemy.orm import relationship
from app.database import Base
import datetime

class Train(Base):
    __tablename__ = "trains"

    train_number = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    source = Column(String, nullable=False)
    destination = Column(String, nullable=False)

    coaches = relationship("Coach", back_populates="train", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="train")

class Coach(Base):
    __tablename__ = "coaches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    train_number = Column(String, ForeignKey("trains.train_number"), nullable=False)
    name = Column(String, nullable=False)  # e.g., S1, S2, B1
    class_type = Column(String, nullable=False)  # e.g., SL, 3A, 2A
    total_seats = Column(Integer, default=72)

    train = relationship("Train", back_populates="coaches")
    seats = relationship("Seat", back_populates="coach", cascade="all, delete-orphan")

class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coach_id = Column(Integer, ForeignKey("coaches.id"), nullable=False)
    seat_number = Column(Integer, nullable=False)
    berth_type = Column(String, nullable=False)  # LOWER, MIDDLE, UPPER, SIDE_LOWER, SIDE_UPPER
    status = Column(String, default="VACANT")  # VACANT, LOCKED, BOOKED, RAC_FULL
    booking_id = Column(String, ForeignKey("bookings.id"), nullable=True)

    coach = relationship("Coach", back_populates="seats")
    booking = relationship("Booking", back_populates="seats")

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(String, primary_key=True, index=True)  # transaction id / uuid
    train_number = Column(String, ForeignKey("trains.train_number"), nullable=False)
    booking_date = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="CONFIRMED")  # CONFIRMED, WAITLISTED, CANCELLED
    pnr = Column(String, unique=True, index=True, nullable=True)

    train = relationship("Train", back_populates="bookings")
    seats = relationship("Seat", back_populates="booking")
    passengers = relationship("Passenger", back_populates="booking", cascade="all, delete-orphan")

class Passenger(Base):
    __tablename__ = "passengers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    booking_id = Column(String, ForeignKey("bookings.id"), nullable=False)
    name = Column(String, nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String, nullable=False)
    berth_preference = Column(String, default="NONE")  # LOWER, MIDDLE, UPPER, SIDE_LOWER, SIDE_UPPER, NONE
    
    # Booking allocation outcome fields
    status = Column(String, default="WL")  # CNF, RAC, WL
    coach_name = Column(String, nullable=True)
    seat_number = Column(Integer, nullable=True)
    berth_allocated = Column(String, nullable=True)  # e.g. LOWER, Middle, or Side Lower (RAC)

    booking = relationship("Booking", back_populates="passengers")
    waitlist_entry = relationship("WaitlistQueue", back_populates="passenger", uselist=False, cascade="all, delete-orphan")

class WaitlistQueue(Base):
    __tablename__ = "waitlist_queues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    train_number = Column(String, ForeignKey("trains.train_number"), nullable=False)
    passenger_id = Column(Integer, ForeignKey("passengers.id"), nullable=False)
    queue_type = Column(String, nullable=False)  # GNWL, PQWL, RLWL, RAC_QUEUE
    priority_number = Column(Integer, nullable=False)
    status = Column(String, default="WAITING")  # WAITING, UPGRADED, CANCELLED

    passenger = relationship("Passenger", back_populates="waitlist_entry")
