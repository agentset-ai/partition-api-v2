import os
from redis import Redis


def get_redis() -> Redis:
    return Redis(
        host=os.getenv("REDIS_HOST"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD"),
        ssl=True,
    )
