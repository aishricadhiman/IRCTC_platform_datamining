import json
import os
import time
import uuid

import redis
from fastapi import HTTPException


# --------------------------------------------------
# Redis Configuration
# --------------------------------------------------
#
# Redis backs two independent features here:
#   1. A distributed lock per train (and, for the Phase 6 per-seat model,
#      per train+class) so that concurrent allocate/release calls hitting
#      different replicas of this service still serialize.
#   2. A short-lived cache for availability reads, since that endpoint is
#      read far more often than seats change.
#
# PostgreSQL remains the authoritative source of truth. The Redis lock is a
# throughput optimisation that keeps contending requests from fighting over
# the same rows; correctness is guaranteed by the SELECT ... FOR UPDATE row
# locks and the surrounding transaction, which still hold even if a Redis
# lock expires early or Redis itself is unavailable mid-flight.

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

CACHE_TTL_SECONDS = 5

# Phase 6 raised this from the original 5000ms: a group allocation can touch
# many seat rows across coaches, and a lock that expires while its holder is
# still mid-transaction would let a second worker in. The row locks below
# still protect correctness, but a longer lease keeps that from happening in
# the first place.
LOCK_TTL_MS = 15000

# Only releases a lock if it still holds the token we set,
# so one request can never release a lock acquired by another
# after its own lock already expired.
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _lock_key(train_id: int) -> str:
    return f"inventory:lock:{train_id}"


def _cache_key(train_id: int) -> str:
    return f"inventory:availability:{train_id}"


def _seat_lock_key(train_id: int, class_type: str) -> str:
    """
    Lock key for the Phase 6 per-seat model, scoped to train + class so that
    allocations for different classes of the same train do not serialise
    against each other.

    This is deliberately a DIFFERENT key from _lock_key() above: the legacy
    counter endpoints and the per-seat endpoints operate on separate tables
    (inventory vs coaches/seats), so they have nothing to serialise against
    one another about until the Phase 8 cutover retires the counter.
    """
    return f"inventory:lock:{train_id}:{class_type}"


def _seat_cache_key(train_id: int, class_type: str) -> str:
    return f"inventory:seat-availability:{train_id}:{class_type}"


class TrainLock:
    """Distributed lock over a train's inventory, backed by Redis SET NX PX."""

    def __init__(self, train_id: int, class_type: str = None):
        if class_type is None:
            self.key = _lock_key(train_id)
        else:
            self.key = _seat_lock_key(train_id, class_type)
        self.token = str(uuid.uuid4())

    def acquire(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:

            if redis_client.set(self.key, self.token, nx=True, px=LOCK_TTL_MS):
                return True

            time.sleep(0.05)

        return False

    def release(self):
        try:
            redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.token)
        except redis.RedisError as error:
            # The work is already committed by this point; a lock we cannot
            # delete will expire on its own via LOCK_TTL_MS. Losing Redis
            # here must not turn a successful allocation into a failure.
            print(f"[locks] could not release {self.key}: {error}")

    def __enter__(self):
        try:
            acquired = self.acquire()
        except redis.RedisError as error:
            # Redis itself is unreachable. Fail closed and say so with the
            # same 503 used for lock contention, rather than letting a raw
            # ConnectionError surface as an opaque 500 - nothing has touched
            # the database at this point, so there is nothing to undo.
            raise HTTPException(
                status_code=503,
                detail=f"Inventory lock service unavailable: {error}"
            )

        if not acquired:
            raise HTTPException(
                status_code=503,
                detail="Could not acquire inventory lock, try again"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def invalidate_availability_cache(train_id: int):
    redis_client.delete(_cache_key(train_id))


def invalidate_seat_availability_cache(train_id: int, class_type: str):
    redis_client.delete(_seat_cache_key(train_id, class_type))
