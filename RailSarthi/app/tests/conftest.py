import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Train, Coach, Seat

@pytest.fixture(scope="function")
def db_session():
    # Use clean in-memory SQLite database for tests
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Seed basic train layout
        train = Train(
            train_number="12002",
            name="Shatabdi Express Test",
            source="NDLS",
            destination="HBJ"
        )
        session.add(train)
        session.commit()
        
        # Add a test Sleeper coach with 8 seats (1 compartment/bay)
        # 1-LOWER, 2-MIDDLE, 3-UPPER, 4-LOWER, 5-MIDDLE, 6-UPPER, 7-SIDE_LOWER, 8-SIDE_UPPER
        coach = Coach(train_number="12002", name="S1", class_type="SL", total_seats=8)
        session.add(coach)
        session.commit()
        
        berths = ["LOWER", "MIDDLE", "UPPER", "LOWER", "MIDDLE", "UPPER", "SIDE_LOWER", "SIDE_UPPER"]
        for idx, berth in enumerate(berths):
            seat = Seat(
                coach_id=coach.id,
                seat_number=idx + 1,
                berth_type=berth,
                status="VACANT"
            )
            session.add(seat)
        session.commit()
        
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
