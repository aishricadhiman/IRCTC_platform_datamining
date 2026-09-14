from app.database import SessionLocal, engine, Base
from app.models import Train, Coach, Seat

def get_berth_type(seat_num: int) -> str:
    remainder = seat_num % 8
    if remainder in (1, 4):
        return "LOWER"
    elif remainder in (2, 5):
        return "MIDDLE"
    elif remainder in (3, 6):
        return "UPPER"
    elif remainder == 7:
        return "SIDE_LOWER"
    else:  # remainder == 0
        return "SIDE_UPPER"

def seed_db():
    # Create all tables if they don't exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Check if trains already exist
        if db.query(Train).first() is not None:
            print("Database already seeded.")
            return

        print("Seeding database...")
        # 1. Create a Train
        shatabdi = Train(
            train_number="12002",
            name="New Delhi Bhopal Shatabdi Express",
            source="NDLS",
            destination="HBJ"
        )
        kerala = Train(
            train_number="12626",
            name="Kerala Express",
            source="NDLS",
            destination="TVC"
        )
        db.add_all([shatabdi, kerala])
        db.commit()

        # 2. Add Coaches & Seats for Shatabdi (e.g. SL and 3A)
        # S1 (Sleeper) coach with 72 seats
        s1 = Coach(train_number="12002", name="S1", class_type="SL", total_seats=72)
        # B1 (AC 3 Tier) coach with 72 seats
        b1 = Coach(train_number="12002", name="B1", class_type="3A", total_seats=72)
        
        # Coaches for Kerala Express
        ks1 = Coach(train_number="12626", name="S1", class_type="SL", total_seats=72)
        
        db.add_all([s1, b1, ks1])
        db.commit()

        # 3. Add seats for the coaches
        for coach in [s1, b1, ks1]:
            seats = []
            for seat_num in range(1, coach.total_seats + 1):
                berth = get_berth_type(seat_num)
                seat = Seat(
                    coach_id=coach.id,
                    seat_number=seat_num,
                    berth_type=berth,
                    status="VACANT"
                )
                seats.append(seat)
            db.add_all(seats)
        db.commit()
        print("Database successfully seeded!")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    seed_db()
