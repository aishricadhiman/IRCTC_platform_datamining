"""
Shared database access.

All services point at the SAME SQLite file to keep this demo runnable without
Postgres. This mirrors having each microservice own its tables in a shared
cluster; in a real deployment each service would instead own a private schema
(or a fully separate database) and talk to other services only over their
APIs, never by reaching into another service's tables directly.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.environ.get(
    "IRCTC_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "irctc.db"),
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False: FastAPI/uvicorn may use different worker threads;
# SQLite still serializes writers internally, giving us safe atomic UPDATEs.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
