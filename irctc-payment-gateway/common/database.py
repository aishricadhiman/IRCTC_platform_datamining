"""
Shared database access.

Defaults to a local SQLite file so this service still runs standalone with
zero external infrastructure - this is what scripts/run_payment_only.py and
tests/test_payment_service.py rely on, and that workflow is unchanged.

Inside the irctc-microservices Docker stack, DATABASE_URL is set to this
service's own PostgreSQL container (irctc_payments on payment-db), matching
the database-per-service pattern every other service there already follows.
Payment Gateway owns this database exclusively - no other service reads or
writes it directly, and Payment Gateway never reads/writes another
service's database.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.environ.get(
    "IRCTC_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "irctc.db"),
)

# DATABASE_URL, when set (as it is in docker-compose.yml), overrides the
# SQLite default entirely - e.g. postgresql://postgres:postgres@payment-db:5432/irctc_payments
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

# check_same_thread is a SQLite-only connect arg (psycopg2 rejects it
# outright) - only pass it when actually running against SQLite.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
