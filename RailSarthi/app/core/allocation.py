from sqlalchemy.orm import Session
from app.models import Train, Coach, Seat, Passenger, Booking
from app.schemas import BookingCreate
from typing import List, Dict, Optional, Tuple
import uuid

def allocate_seats(db: Session, booking_id: str, request: BookingCreate) -> Tuple[str, List[Passenger]]:
    """
    Core seat allocation algorithm.
    Tries to allocate seats for a list of passengers on a train and class.
    Follows priority rules:
      1. Senior citizens (age >= 60) get LOWER berths if available.
      2. Groups/families are kept in the same coach, and same bay if possible.
      3. Fallback to RAC (Side Lower berths) if confirmed seats are full.
      4. Fallback to Waitlist (GNWL) if RAC is full.
    """
    passengers_to_allocate = request.passengers
    num_passengers = len(passengers_to_allocate)
    
    # 1. Fetch all coaches for the train and class
    coaches = db.query(Coach).filter(
        Coach.train_number == request.train_number,
        Coach.class_type == request.class_type
    ).all()
    
    if not coaches:
        raise ValueError(f"No coaches found for train {request.train_number} and class {request.class_type}")

    coach_ids = [c.id for c in coaches]
    
    # Fetch all vacant seats in these coaches
    vacant_seats = db.query(Seat).filter(
        Seat.coach_id.in_(coach_ids),
        Seat.status == "VACANT"
    ).all()

    # Map coach_id to Coach object for easy access
    coach_map = {c.id: c for c in coaches}

    # Group vacant seats by coach (CNF allocation only uses non-SIDE_LOWER seats)
    vacant_by_coach: Dict[int, List[Seat]] = {}
    for seat in vacant_seats:
        if seat.berth_type != "SIDE_LOWER":
            vacant_by_coach.setdefault(seat.coach_id, []).append(seat)

    # 2. Choose the best coach for family/group booking
    # Try to find a coach with enough vacant seats for the whole group
    target_coach_id = None
    for coach_id, seats in vacant_by_coach.items():
        if len(seats) >= num_passengers:
            target_coach_id = coach_id
            break

    # If no single coach has enough seats, we will allocate across coaches (target_coach_id = None)
    
    allocated_passengers = []
    
    # Helper to check if a passenger is senior citizen
    def is_senior(passenger) -> bool:
        return passenger.age >= 60

    # We will process passengers one by one
    for p_req in passengers_to_allocate:
        # Create passenger record
        db_passenger = Passenger(
            booking_id=booking_id,
            name=p_req.name,
            age=p_req.age,
            gender=p_req.gender,
            berth_preference=p_req.berth_preference,
            status="WL"
        )
        db.add(db_passenger)
        db.flush() # gets db_passenger.id

        allocated = False

        # If we have a target coach, prioritize searching there. Otherwise search all relevant coaches.
        search_coach_ids = [target_coach_id] if target_coach_id else list(coach_map.keys())
        
        # Try to find a Confirmed (CNF) seat
        seat_found = None

        # Pass 1: Try to find seat satisfying priority & preference
        pref = "LOWER" if is_senior(p_req) else p_req.berth_preference.upper()
        if pref != "NONE":
            for cid in search_coach_ids:
                if cid in vacant_by_coach:
                    for s in vacant_by_coach[cid]:
                        if s.berth_type == pref:
                            seat_found = s
                            break
                if seat_found:
                    break

        # Pass 2: Try to find any seat matching preference if priority lower wasn't found or wasn't senior
        if not seat_found and p_req.berth_preference.upper() != "NONE":
            pref = p_req.berth_preference.upper()
            for cid in search_coach_ids:
                if cid in vacant_by_coach:
                    for s in vacant_by_coach[cid]:
                        if s.berth_type == pref:
                            seat_found = s
                            break
                if seat_found:
                    break

        # Pass 3: Grab any vacant seat
        if not seat_found:
            for cid in search_coach_ids:
                if cid in vacant_by_coach and vacant_by_coach[cid]:
                    seat_found = vacant_by_coach[cid][0]
                    break

        # If we found a confirmed seat, book it
        if seat_found:
            seat_found.status = "BOOKED"
            seat_found.booking_id = booking_id
            
            db_passenger.status = "CNF"
            db_passenger.coach_name = coach_map[seat_found.coach_id].name
            db_passenger.seat_number = seat_found.seat_number
            db_passenger.berth_allocated = seat_found.berth_type
            
            # Remove from vacant list
            vacant_by_coach[seat_found.coach_id].remove(seat_found)
            allocated = True

        # 3. Fallback to RAC (Side Lower Berths)
        if not allocated:
            # RAC berths are SIDE_LOWER berths.
            # We look for SIDE_LOWER seats that are either:
            # - VACANT (meaning we can put the first RAC passenger on it)
            # - Already have status = "RAC_ONE" (meaning 1 passenger is assigned, we can add the second and set status = "RAC_FULL")
            rac_seat = None
            
            # Let's search all coaches for a SIDE_LOWER that is VACANT or RAC_ONE
            # First, check if there's any vacant SIDE_LOWER we can convert to RAC
            for cid in coach_map.keys():
                # Fetch all SIDE_LOWER seats for this coach
                side_lowers = db.query(Seat).filter(
                    Seat.coach_id == cid,
                    Seat.berth_type == "SIDE_LOWER"
                ).all()
                
                # Check for RAC_ONE first (sharing a berth that already has one person)
                for s in side_lowers:
                    if s.status == "RAC_ONE":
                        rac_seat = s
                        break
                if rac_seat:
                    break
                
                # If not found, look for VACANT side lower to start a new RAC pair
                for s in side_lowers:
                    if s.status == "VACANT":
                        rac_seat = s
                        break
                if rac_seat:
                    break
            
            if rac_seat:
                if rac_seat.status == "VACANT":
                    # First RAC passenger on this seat
                    rac_seat.status = "RAC_ONE"
                    rac_seat.booking_id = booking_id
                else:
                    # Second RAC passenger on this seat, now it's full
                    rac_seat.status = "RAC_FULL"
                    # Append booking_id if we want, or keep the first one. Let's keep it locked.
                
                db_passenger.status = "RAC"
                db_passenger.coach_name = coach_map[rac_seat.coach_id].name
                db_passenger.seat_number = rac_seat.seat_number
                db_passenger.berth_allocated = "SIDE_LOWER (RAC)"
                allocated = True
                
                # If it was vacant, remove it from the general vacant seats map if it was there
                if rac_seat.coach_id in vacant_by_coach and rac_seat in vacant_by_coach[rac_seat.coach_id]:
                    vacant_by_coach[rac_seat.coach_id].remove(rac_seat)

        # 4. Fallback to Waitlist
        if not allocated:
            from app.models import WaitlistQueue
            
            # Count current waitlisted passengers to assign priority number
            current_wl_count = db.query(WaitlistQueue).filter(
                WaitlistQueue.train_number == request.train_number,
                WaitlistQueue.status == "WAITING"
            ).count()
            
            priority = current_wl_count + 1
            
            db_passenger.status = "WL"
            db_passenger.coach_name = None
            db_passenger.seat_number = None
            db_passenger.berth_allocated = None
            
            # Create waitlist queue entry
            wl_entry = WaitlistQueue(
                train_number=request.train_number,
                passenger_id=db_passenger.id,
                queue_type="GNWL",
                priority_number=priority,
                status="WAITING"
            )
            db.add(wl_entry)
        
        allocated_passengers.append(db_passenger)
        
    db.commit()
    
    # Calculate overall booking status
    statuses = [p.status for p in allocated_passengers]
    if all(s in ("CNF", "RAC") for s in statuses):
        overall_status = "CONFIRMED"
    elif all(s == "WL" for s in statuses):
        overall_status = "WAITLISTED"
    else:
        overall_status = "PARTIALLY_CONFIRMED"
        
    return overall_status, allocated_passengers
