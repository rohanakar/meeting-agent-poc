from functools import cache

from redis import Redis
from rq import Queue

from .config import get_settings

BOTS_QUEUE = "bots"
PIPELINE_QUEUE = "pipeline"


@cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_queue(name: str) -> Queue:
    return Queue(name, connection=get_redis())
