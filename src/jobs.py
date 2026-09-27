import uuid
from typing import TypeVar
from pydantic import BaseModel
from .redis_client import get_redis

# Job records keep request data out of Modal function inputs: only the job id is
# passed to .spawn()
JOB_TTL_SECONDS = 3 * 60 * 60  # 3 hours

T = TypeVar("T", bound=BaseModel)


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def store_job(request: BaseModel) -> str:
    job_id = str(uuid.uuid4())
    get_redis().set(job_key(job_id), request.model_dump_json(), ex=JOB_TTL_SECONDS)
    return job_id


def load_job(job_id: str, model: type[T]) -> T | None:
    """Loads the job record. Returns None if it doesn't exist.

    The record is kept until delete_job, so an input that Modal retries (e.g.
    after a container failure) can still load it.
    """
    raw = get_redis().get(job_key(job_id))
    if raw is None:
        return None

    return model.model_validate_json(raw)


def delete_job(job_id: str) -> None:
    get_redis().delete(job_key(job_id))
