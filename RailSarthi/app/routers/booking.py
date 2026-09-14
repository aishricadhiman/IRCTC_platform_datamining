from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import BookingCreate, BookingResponse
from app.models import Booking
from app.core.allocation import allocate_seats
import uuid

router = APIRouter(prefix="/bookings", tags=["Bookings"])

@router.post("/allocate", response_model=BookingResponse)
def create_booking_and_allocate(request: BookingCreate, db: Session = Depends(get_db)):
    """
    Allocates seats for passengers and generates a mock PNR.
    """
    # 1. Create a booking ID (transaction ID)
    booking_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    pnr = f"PNR-{uuid.uuid4().hex[:10].upper()}"
    
    # Create the Booking record in DB
    db_booking = Booking(
        id=booking_id,
        train_number=request.train_number,
        status="CONFIRMED",
        pnr=pnr
    )
    db.add(db_booking)
    db.commit()

    try:
        overall_status, passengers = allocate_seats(db, booking_id, request)
        
        # Update overall booking status in the DB
        db_booking.status = overall_status
        db.commit()
        db.refresh(db_booking)
        
        return BookingResponse(
            booking_id=db_booking.id,
            train_number=db_booking.train_number,
            status=db_booking.status,
            pnr=db_booking.pnr,
            passengers=passengers
        )
    except ValueError as e:
        # Rollback and clean up booking if validation fails
        db.delete(db_booking)
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

from app.models import Coach, Seat, WaitlistQueue, Passenger

@router.get("/state")
def get_train_state(train_number: str, class_type: str, db: Session = Depends(get_db)):
    # Find coaches
    coaches = db.query(Coach).filter(
        Coach.train_number == train_number,
        Coach.class_type == class_type
    ).all()
    if not coaches:
        return {"coach_name": "N/A", "seats": [], "active_bookings": [], "waitlist": []}
    
    coach = coaches[0] # Show the first coach
    seats = db.query(Seat).filter(Seat.coach_id == coach.id).order_by(Seat.seat_number.asc()).all()
    
    # Get waitlist
    wl_list = db.query(WaitlistQueue).join(Passenger).filter(
        WaitlistQueue.train_number == train_number,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).all()
    
    # Get bookings
    active_bookings = db.query(Booking).filter(
        Booking.train_number == train_number
    ).all()
    
    return {
        "coach_name": coach.name,
        "seats": [
            {
                "seat_number": s.seat_number,
                "berth_type": s.berth_type,
                "status": s.status,
                "booking_id": s.booking_id
            } for s in seats
        ],
        "active_bookings": [
            {
                "booking_id": b.id,
                "status": b.status,
                "pnr": b.pnr,
                "passengers": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "status": p.status,
                        "coach_name": p.coach_name,
                        "seat_number": p.seat_number,
                        "berth_allocated": p.berth_allocated
                    } for p in b.passengers
                ]
            } for b in active_bookings if b.status != "CANCELLED"
        ],
        "waitlist": [
            {
                "priority": w.priority_number,
                "passenger_name": w.passenger.name,
                "passenger_id": w.passenger_id,
                "age": w.passenger.age,
                "gender": w.passenger.gender,
                "status": w.passenger.status,
                "queue_type": w.queue_type
            } for w in wl_list
        ]
    }

