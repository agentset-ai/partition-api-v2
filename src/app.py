import os
import modal
from .region import (
    SECRET_NAME,
    app_name,
    check_deploy_target,
    get_region,
    required_secret_keys,
)

REGION = get_region()

if modal.is_local():
    check_deploy_target(REGION, os.getenv("MODAL_ENVIRONMENT"), modal.__version__)

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(
        "fastapi[standard]",
        "markitdown[docx,outlook,pptx,xls,xlsx]",
        "pydantic",
        "redis",
        "datalab-python-sdk",
        "chonkie[all]",
        "firecrawl-py",
        "cuid2",
        "python-magic",
        "boto3",
        "youtube-transcript-api",
        "pandas",
    )
    .apt_install("libmagic1")
)

if REGION == "eu":
    # containers read the region from the image, independent of the secret
    image = image.env({"AGENTSET_REGION": REGION})

app = modal.App(
    name=app_name(REGION),
    image=image,
    secrets=[
        modal.Secret.from_name(
            SECRET_NAME,
            # FIRECRAWL_API_KEY, YOUTUBE_API_KEY, PROXY_USERNAME and PROXY_PASSWORD
            # are optional: crawl and YouTube ingestion are disabled on EU
            required_keys=required_secret_keys(REGION),
        )
    ],
)
