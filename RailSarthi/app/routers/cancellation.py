from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import CancellationRequest, CancellationResponse
from app.models import Booking
from app.core.waitlist import process_cancellation

router = APIRouter(prefix="/bookings", tags=["Cancellations"])

@router.post("/cancel", response_model=CancellationResponse)
def cancel_ticket(request: CancellationRequest, db: Session = Depends(get_db)):
    """
    Cancels specified passenger tickets and triggers waitlist/RAC promotion.
    """
    # Verify booking exists
    booking = db.query(Booking).filter(Booking.id == request.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    try:
        cancelled_passenger_ids = process_cancellation(
            db=db,
            booking_id=request.booking_id,
            passenger_ids=request.passenger_ids
        )
        
        if not cancelled_passenger_ids:
            raise HTTPException(
                status_code=400, 
                detail="No active passengers found with the provided IDs in this booking"
            )
            
        # Check if booking is fully cancelled
        active_passengers = [p for p in booking.passengers if p.status != "CANCELLED"]
        if not active_passengers:
            booking.status = "CANCELLED"
            db.commit()
            
        return CancellationResponse(
            booking_id=booking.id,
            status=booking.status,
            cancelled_passenger_ids=cancelled_passenger_ids,
            refund_status="INITIATED_SUCCESSFULLY"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
