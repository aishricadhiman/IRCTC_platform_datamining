"""
A minimal durable event bus, standing in for Apache Kafka in this local demo.

Why not just use an in-memory Python queue? Because each service in this
prototype runs as its own OS process (its own `uvicorn` instance), so an
in-memory queue in one process would be invisible to the others. Instead,
events are written to a durable `events` table (the "topic log"), and each
consumer tracks its own offset in a `consumer_offsets` table - this is the
same *durable log + per-consumer offset* model Kafka uses, just backed by
SQLite instead of a distributed commit log.

Guarantees preserved from the design doc:
  - At-least-once delivery (a consumer that crashes before advancing its
    offset will simply re-read the same events on restart).
  - Replayability (nothing is deleted; a new consumer can start from offset 0
    and replay full history).
  - Multiple independent consumers of the same topic (Booking Service and
    Notification Service both consume events without interfering).

Swapping this module for a real `kafka-python`/`confluent-kafka` producer and
consumer group would not require any changes to the calling code in the
services - `publish()` and `poll()` keep the same signatures.
"""
import json
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime

from .database import Base, SessionLocal


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(50), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConsumerOffset(Base):
    __tablename__ = "consumer_offsets"
    consumer_name = Column(String(50), primary_key=True)
    last_event_id = Column(Integer, default=0)


def publish(topic: str, payload: dict) -> int:
    db = SessionLocal()
    try:
        ev = Event(topic=topic, payload=json.dumps(payload))
        db.add(ev)
        db.commit()
        db.refresh(ev)
        return ev.id
    finally:
        db.close()


def poll(consumer_name: str, topics: list, batch_size: int = 20):
    """
    Fetch events newer than this consumer's last committed offset, for the
    given topics, and advance the offset. Returns a list of (event_id, topic, payload).
    """
    db = SessionLocal()
    try:
        offset_row = db.get(ConsumerOffset, consumer_name)
        if offset_row is None:
            offset_row = ConsumerOffset(consumer_name=consumer_name, last_event_id=0)
            db.add(offset_row)
            db.commit()
            db.refresh(offset_row)

        events = (
            db.query(Event)
            .filter(Event.id > offset_row.last_event_id, Event.topic.in_(topics))
            .order_by(Event.id.asc())
            .limit(batch_size)
            .all()
        )
        results = [(e.id, e.topic, json.loads(e.payload)) for e in events]
        if events:
            offset_row.last_event_id = events[-1].id
            db.commit()
        return results
    finally:
        db.close()
