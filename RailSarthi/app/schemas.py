from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class PassengerCreate(BaseModel):
    name: str
    age: int
    gender: str
    berth_preference: str = "NONE"  # LOWER, MIDDLE, UPPER, SIDE_LOWER, SIDE_UPPER, NONE

class BookingCreate(BaseModel):
    train_number: str
    class_type: str  # SL, 3A, 2A
    passengers: List[PassengerCreate]

class PassengerAllocationResult(BaseModel):
    id: int
    name: str
    status: str  # CNF, RAC, WL
    coach_name: Optional[str] = None
    seat_number: Optional[int] = None
    berth_allocated: Optional[str] = None

    class Config:
        orm_mode = True
        from_attributes = True  # Pydantic v2 support

class BookingResponse(BaseModel):
    booking_id: str
    train_number: str
    status: str  # CONFIRMED, PARTIALLY_CONFIRMED, WAITLISTED
    pnr: Optional[str] = None
    passengers: List[PassengerAllocationResult]

    class Config:
        orm_mode = True
        from_attributes = True

class CancellationRequest(BaseModel):
    booking_id: str
    passenger_ids: List[int]

class CancellationResponse(BaseModel):
    booking_id: str
    status: str  # CANCELLED or PARTIALLY_CANCELLED
    cancelled_passenger_ids: List[int]
    refund_status: str
