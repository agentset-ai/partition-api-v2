import modal

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

app = modal.App(
    name="agentset-ingest-v3",
    image=image,
    secrets=[
        modal.Secret.from_name(
            "partitioner-secrets",
            required_keys=[
                "DATALAB_API_KEY",
                "REDIS_HOST",
                "REDIS_PORT",
                "REDIS_PASSWORD",
                "AGENTSET_API_KEY",
                "FIRECRAWL_API_KEY",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "R2_BUCKET_NAME",
                "R2_CHUNKS_BUCKET_NAME",
                "R2_ENDPOINT_URL",
                "R2_PUBLIC_URL",
                "YOUTUBE_API_KEY",
                "PROXY_USERNAME",
                "PROXY_PASSWORD",
            ],
        )
    ],
)
