from sqlalchemy.orm import Session
from app.models import Passenger, Seat, WaitlistQueue, Booking, Coach
from typing import List

def process_cancellation(db: Session, booking_id: str, passenger_ids: List[int]) -> List[int]:
    """
    Processes ticket cancellation for specific passengers.
    Triggers cascading waitlist/RAC promotions:
      1. If a CNF seat is freed, promote the first RAC passenger to CNF.
      2. If an RAC seat is freed, promote the first WL passenger to RAC.
      3. If a WL seat is cancelled, reorder the remaining waitlist priorities.
    Returns the list of successfully cancelled passenger IDs.
    """
    cancelled_ids = []
    
    # 1. Fetch the booking
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise ValueError(f"Booking {booking_id} not found")

    # Fetch passengers to cancel
    passengers = db.query(Passenger).filter(
        Passenger.id.in_(passenger_ids),
        Passenger.booking_id == booking_id,
        Passenger.status != "CANCELLED"
    ).all()

    for passenger in passengers:
        old_status = passenger.status
        old_coach = passenger.coach_name
        old_seat_num = passenger.seat_number
        
        # Mark passenger as cancelled
        passenger.status = "CANCELLED"
        passenger.coach_name = None
        passenger.seat_number = None
        passenger.berth_allocated = None
        
        cancelled_ids.append(passenger.id)
        db.flush()

        if old_status == "CNF":
            # Confirmed seat is now vacant!
            # Find the physical seat record
            coach = db.query(Coach).filter(
                Coach.train_number == booking.train_number,
                Coach.name == old_coach
            ).first()
            
            if coach:
                seat = db.query(Seat).filter(
                    Seat.coach_id == coach.id,
                    Seat.seat_number == old_seat_num
                ).first()
                
                if seat:
                    # Promote RAC passenger to this CNF seat
                    promoted_rac = promote_first_rac_to_cnf(db, booking.train_number, seat)
                    if not promoted_rac:
                        # If no RAC passengers exist, the seat becomes VACANT
                        seat.status = "VACANT"
                        seat.booking_id = None
                        
                        # Also check if we can promote a waitlisted passenger directly to CNF
                        promote_first_wl_to_cnf(db, booking.train_number, seat)

        elif old_status == "RAC":
            # RAC slot is freed.
            # Find the RAC seat record
            coach = db.query(Coach).filter(
                Coach.train_number == booking.train_number,
                Coach.name == old_coach
            ).first()
            
            if coach:
                seat = db.query(Seat).filter(
                    Seat.coach_id == coach.id,
                    Seat.seat_number == old_seat_num
                ).first()
                
                if seat:
                    # Update seat status
                    if seat.status == "RAC_FULL":
                        seat.status = "RAC_ONE"
                    elif seat.status == "RAC_ONE":
                        seat.status = "VACANT"
                        seat.booking_id = None
                    
                    # Promote first waitlist to the vacant RAC slot
                    promote_first_wl_to_rac(db, booking.train_number, seat)

        elif old_status == "WL":
            # Remove from waitlist queue
            wl_entry = db.query(WaitlistQueue).filter(
                WaitlistQueue.passenger_id == passenger.id
            ).first()
            if wl_entry:
                wl_entry.status = "CANCELLED"
                db.flush()
                # Reorder the remaining waitlist priorities
                reorder_waitlist(db, booking.train_number)

    db.commit()
    return cancelled_ids

def promote_first_rac_to_cnf(db: Session, train_number: str, confirmed_seat: Seat) -> bool:
    """
    Finds the first RAC passenger and upgrades them to CNF.
    Freed RAC slot is handled by caller or filled by waitlist.
    """
    # Fetch coaches of the same class type to identify RAC passengers
    target_coach = db.query(Coach).filter(Coach.id == confirmed_seat.coach_id).first()
    coaches = db.query(Coach).filter(
        Coach.train_number == train_number,
        Coach.class_type == target_coach.class_type
    ).all()
    coach_names = [c.name for c in coaches]

    # Find first RAC passenger in these coaches
    first_rac_passenger = db.query(Passenger).join(Booking).filter(
        Booking.train_number == train_number,
        Passenger.status == "RAC",
        Passenger.coach_name.in_(coach_names)
    ).order_by(Passenger.id.asc()).first()

    if first_rac_passenger:
        # Save old RAC seat details
        old_rac_coach_name = first_rac_passenger.coach_name
        old_rac_seat_num = first_rac_passenger.seat_number

        # Upgrade passenger to CNF on the freed seat
        first_rac_passenger.status = "CNF"
        first_rac_passenger.coach_name = target_coach.name
        first_rac_passenger.seat_number = confirmed_seat.seat_number
        first_rac_passenger.berth_allocated = confirmed_seat.berth_type

        # Update confirmed seat status
        confirmed_seat.status = "BOOKED"
        confirmed_seat.booking_id = first_rac_passenger.booking_id
        db.flush()

        # Handle the freed RAC slot
        old_rac_coach = db.query(Coach).filter(
            Coach.train_number == train_number,
            Coach.name == old_rac_coach_name
        ).first()
        
        if old_rac_coach:
            rac_seat = db.query(Seat).filter(
                Seat.coach_id == old_rac_coach.id,
                Seat.seat_number == old_rac_seat_num
            ).first()

            if rac_seat:
                if rac_seat.status == "RAC_FULL":
                    rac_seat.status = "RAC_ONE"
                elif rac_seat.status == "RAC_ONE":
                    rac_seat.status = "VACANT"
                    rac_seat.booking_id = None
                
                # Try to promote a waitlisted passenger to RAC
                promote_first_wl_to_rac(db, train_number, rac_seat)
        return True
    return False

def promote_first_wl_to_cnf(db: Session, train_number: str, confirmed_seat: Seat):
    """
    Directly promotes a waitlist passenger to CNF if no RAC passengers are present.
    """
    target_coach = db.query(Coach).filter(Coach.id == confirmed_seat.coach_id).first()
    
    first_wl_queue = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_number == train_number,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).first()

    if first_wl_queue:
        passenger = db.query(Passenger).filter(Passenger.id == first_wl_queue.passenger_id).first()
        if passenger:
            passenger.status = "CNF"
            passenger.coach_name = target_coach.name
            passenger.seat_number = confirmed_seat.seat_number
            passenger.berth_allocated = confirmed_seat.berth_type

            confirmed_seat.status = "BOOKED"
            confirmed_seat.booking_id = passenger.booking_id

            first_wl_queue.status = "UPGRADED"
            db.flush()
            reorder_waitlist(db, train_number)

def promote_first_wl_to_rac(db: Session, train_number: str, rac_seat: Seat):
    """
    Promotes the first waitlisted passenger to RAC on a freed RAC slot.
    """
    first_wl_queue = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_number == train_number,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).first()

    if first_wl_queue:
        passenger = db.query(Passenger).filter(Passenger.id == first_wl_queue.passenger_id).first()
        coach = db.query(Coach).filter(Coach.id == rac_seat.coach_id).first()
        
        if passenger and coach:
            passenger.status = "RAC"
            passenger.coach_name = coach.name
            passenger.seat_number = rac_seat.seat_number
            passenger.berth_allocated = "SIDE_LOWER (RAC)"

            if rac_seat.status == "VACANT":
                rac_seat.status = "RAC_ONE"
                rac_seat.booking_id = passenger.booking_id
            elif rac_seat.status == "RAC_ONE":
                rac_seat.status = "RAC_FULL"

            first_wl_queue.status = "UPGRADED"
            db.flush()
            reorder_waitlist(db, train_number)

def reorder_waitlist(db: Session, train_number: str):
    """
    Updates the priority sequence for remaining active waitlist passengers.
    """
    active_queues = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_number == train_number,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).all()

    for idx, wl in enumerate(active_queues):
        wl.priority_number = idx + 1
    db.flush()
