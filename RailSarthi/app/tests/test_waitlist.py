from app.core.allocation import allocate_seats
from app.core.waitlist import process_cancellation
from app.schemas import BookingCreate, PassengerCreate
from app.models import Passenger, WaitlistQueue, Seat, Booking

def test_rac_and_waitlist_cascade(db_session):
    # Create booking records in DB first
    db_session.add(Booking(id="TXN-CNF", train_number="12002", status="CONFIRMED"))
    db_session.add(Booking(id="TXN-RAC", train_number="12002", status="CONFIRMED"))
    db_session.add(Booking(id="TXN-WL", train_number="12002", status="CONFIRMED"))
    db_session.commit()

    # 1. Book 7 passengers to fill all non-SIDE_LOWER CNF seats (seats 1-6, and 8)
    request1 = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name=f"P{i}", age=30, gender="M", berth_preference="NONE")
            for i in range(1, 8)
        ]
    )
    status1, passengers1 = allocate_seats(db_session, "TXN-CNF", request1)
    assert status1 == "CONFIRMED"
    for p in passengers1:
        assert p.status == "CNF"

    # 2. Book 2 more passengers - they should go to RAC (Seat 7 - SIDE_LOWER)
    request2 = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name="P8", age=30, gender="M", berth_preference="NONE"),
            PassengerCreate(name="P9", age=30, gender="M", berth_preference="NONE")
        ]
    )
    status2, passengers2 = allocate_seats(db_session, "TXN-RAC", request2)
    assert status2 == "CONFIRMED"
    for p in passengers2:
        assert p.status == "RAC"
        assert p.seat_number == 7

    # 3. Book 1 more passenger - should go to GNWL (Waitlist)
    request3 = BookingCreate(
        train_number="12002",
        class_type="SL",
        passengers=[
            PassengerCreate(name="P10", age=30, gender="M", berth_preference="NONE")
        ]
    )
    status3, passengers3 = allocate_seats(db_session, "TXN-WL", request3)
    assert status3 == "WAITLISTED"
    assert passengers3[0].status == "WL"
    
    # Check waitlist database record
    wl_entry = db_session.query(WaitlistQueue).filter(
        WaitlistQueue.passenger_id == passengers3[0].id
    ).first()
    assert wl_entry is not None
    assert wl_entry.priority_number == 1
    assert wl_entry.status == "WAITING"

    # 4. Cancel Passenger 1 (P1, CNF, seat_number 1)
    p1_id = passengers1[0].id
    cancelled_ids = process_cancellation(db_session, "TXN-CNF", [p1_id])
    assert cancelled_ids == [p1_id]

    # Refresh objects from DB to check states
    db_session.expire_all()
    
    # P1 should be CANCELLED
    p1 = db_session.query(Passenger).filter(Passenger.id == p1_id).first()
    assert p1.status == "CANCELLED"
    
    # P8 (first RAC passenger) should now be upgraded to CNF on seat 1
    p8 = db_session.query(Passenger).filter(Passenger.name == "P8").first()
    assert p8.status == "CNF"
    assert p8.seat_number == 1
    
    # P10 (first WL passenger) should now be upgraded to RAC on seat 7
    p10 = db_session.query(Passenger).filter(Passenger.name == "P10").first()
    assert p10.status == "RAC"
    assert p10.seat_number == 7

    # The waitlist queue entry for P10 should be UPGRADED
    wl_entry_p10 = db_session.query(WaitlistQueue).filter(
        WaitlistQueue.passenger_id == p10.id
    ).first()
    assert wl_entry_p10.status == "UPGRADED"
