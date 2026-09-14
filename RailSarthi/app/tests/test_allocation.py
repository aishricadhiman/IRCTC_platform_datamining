from app.core.allocation import allocate_seats
from app.schemas import BookingCreate, PassengerCreate
from app.models import Seat, Passenger

def test_allocate_seats_simple(db_session):
    # Prepare booking request
    request = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name="Rajesh", age=30, gender="M", berth_preference="NONE")
        ]
    )
    
    status, passengers = allocate_seats(db_session, "TEST-TXN-1", request)
    
    assert status == "CONFIRMED"
    assert len(passengers) == 1
    assert passengers[0].status == "CNF"
    assert passengers[0].coach_name == "S1"
    assert passengers[0].seat_number is not None

def test_allocate_seats_preference_lower(db_session):
    request = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name="Amit", age=35, gender="M", berth_preference="UPPER")
        ]
    )
    
    status, passengers = allocate_seats(db_session, "TEST-TXN-2", request)
    assert status == "CONFIRMED"
    assert passengers[0].berth_allocated == "UPPER"

def test_allocate_seats_senior_priority(db_session):
    # Senior citizen should get LOWER berth automatically even if preference is NONE
    request = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name="Shanti", age=65, gender="F", berth_preference="NONE")
        ]
    )
    
    status, passengers = allocate_seats(db_session, "TEST-TXN-3", request)
    assert status == "CONFIRMED"
    assert passengers[0].berth_allocated == "LOWER"
