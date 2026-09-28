import hashlib
import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from .errors import PartitionError

REGIONS = ("us", "eu")

US_APP_NAME = "agentset-ingest-v3"
EU_APP_NAME = "agentset-ingest-eu"
SECRET_NAME = "partitioner-secrets"

# EU apps are deployed to their own Modal environment, where secrets are scoped
EU_MODAL_ENVIRONMENT = "eu"
EU_FUNCTION_REGION = "eu"
EU_ROUTING_REGION = "eu-west"
# routing_region is supported from this Modal client version
EU_MIN_MODAL_VERSION = (1, 4, 3)

REQUIRED_SECRET_KEYS = [
    "DATALAB_API_KEY",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_PASSWORD",
    "AGENTSET_API_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_CHUNKS_BUCKET_NAME",
    "R2_ENDPOINT_URL",
    "R2_PUBLIC_URL",
]
EU_REQUIRED_SECRET_KEYS = [
    "AGENTSET_REGION",
    "DATALAB_PROCESSING_LOCATION",
    "FIRECRAWL_API_KEY",
]


# sha256 (hex) of hosts that belong to the US deployment and must never be
# configured on EU. Hashes keep the names out of the repo.
US_ONLY_HOST_SHA256 = (
    # Redis host (compare REDIS_HOST)
    "56afe9305397d3c4f91710cdf8d3873902a7b12d935e6369b189bb580f3c376d",
)

EU_R2_ENDPOINT_SUFFIX = ".eu.r2.cloudflarestorage.com"


def get_region() -> str:
    region = os.getenv("AGENTSET_REGION") or "us"
    if region not in REGIONS:
        raise ValueError("AGENTSET_REGION must be one of: us, eu")
    return region


def is_eu() -> bool:
    return get_region() == "eu"


def app_name(region: str) -> str:
    return EU_APP_NAME if region == "eu" else US_APP_NAME


def required_secret_keys(region: str) -> list[str]:
    if region == "eu":
        return [*REQUIRED_SECRET_KEYS, *EU_REQUIRED_SECRET_KEYS]
    return list(REQUIRED_SECRET_KEYS)


def function_options(region: str) -> dict:
    if region == "eu":
        return {"region": EU_FUNCTION_REGION}
    return {}


def web_function_options(region: str) -> dict:
    # routing_region can only be set on a function's first deploy
    if region == "eu":
        return {"region": EU_FUNCTION_REGION, "routing_region": EU_ROUTING_REGION}
    return {}


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def check_deploy_target(
    region: str, modal_environment: str | None, modal_version: str
) -> None:
    if region == "eu":
        if modal_environment != EU_MODAL_ENVIRONMENT:
            raise RuntimeError(
                "EU deploys must target the Modal environment 'eu' (set MODAL_ENVIRONMENT=eu)"
            )
        if _version_tuple(modal_version) < EU_MIN_MODAL_VERSION:
            raise RuntimeError(
                "EU deploys need modal>=1.4.3 for routing_region (see the README)"
            )
    elif modal_environment == EU_MODAL_ENVIRONMENT:
        raise RuntimeError(
            "The Modal environment 'eu' only accepts EU deploys (set AGENTSET_REGION=eu)"
        )


def is_us_only_host(host: str) -> bool:
    digest = hashlib.sha256(host.strip().lower().encode()).hexdigest()
    return digest in US_ONLY_HOST_SHA256


def eu_config_issues(env: Mapping[str, str]) -> list[str]:
    """Where EU content is written: R2, Redis and Datalab. Names variables only."""
    issues = []

    r2_host = urlparse(env.get("R2_ENDPOINT_URL") or "").hostname or ""
    if not r2_host.endswith(EU_R2_ENDPOINT_SUFFIX):
        issues.append(
            "R2_ENDPOINT_URL must be an EU jurisdiction R2 endpoint (*.eu.r2.cloudflarestorage.com)"
        )

    redis_host = env.get("REDIS_HOST") or ""
    if not redis_host:
        issues.append("REDIS_HOST is required in the EU region")
    elif is_us_only_host(redis_host):
        issues.append("REDIS_HOST points to a US-only host")

    if env.get("DATALAB_PROCESSING_LOCATION") != "eu":
        issues.append('DATALAB_PROCESSING_LOCATION must be "eu" in the EU region')

    return issues


def check_eu_config() -> bool:
    """On EU, prints the configuration issues and returns False if there are any."""
    if not is_eu():
        return True

    issues = eu_config_issues(os.environ)
    if issues:
        print("Invalid EU region configuration: " + "; ".join(issues))
    return not issues


def log_error(message: str, error: BaseException, **ids: str | int | None) -> None:
    """Logs the error type, HTTP status and the given IDs, never the error message."""
    parts = [f"{message}: {type(error).__name__}"]

    if isinstance(error, PartitionError):
        parts.append(f"code={error.code}")

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        parts.append(f"status={status_code}")

    for key, value in ids.items():
        if value is not None:
            parts.append(f"{key}={value}")

    print(" ".join(parts))
