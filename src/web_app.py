from typing import Annotated
from fastapi import FastAPI, Header, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import modal
import os

from .schema import IngestRequest, CrawlRequest, YouTubeRequest
from .ingest import ingest_operation
from .crawl import crawl_operation
from .yt import youtube_operation
from .app import app, REGION
from .jobs import store_job
from .region import check_eu_config, is_eu, log_error, web_function_options

web_app = FastAPI()


@app.function(**web_function_options(REGION))
@modal.asgi_app()
def partition_api():
    return web_app


@web_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if not is_eu():
        return await request_validation_exception_handler(request, exc)

    # on EU, don't echo request input back in validation errors
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]
        },
    )


def _not_available_in_region(operation: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "status": status.HTTP_403_FORBIDDEN,
            "message": f"{operation} is not available in this region",
        },
    )


def _failed_to_queue() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": status.HTTP_503_SERVICE_UNAVAILABLE,
            "message": "Failed to queue job",
        },
    )


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"status": status.HTTP_404_NOT_FOUND, "message": "Not found"},
    )


@web_app.post("/ingest")
async def ingest(
    request: IngestRequest,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        if not check_eu_config():
            return _failed_to_queue()

        # spawn inputs only carry the job id; the request is kept in Redis
        try:
            job_id = store_job(request)
        except Exception as e:
            log_error(
                "Failed to store ingest job",
                e,
                namespace_id=request.namespace_id,
                document_id=request.document_id,
            )
            return _failed_to_queue()

        call = ingest_operation.spawn(job_id)
    else:
        call = ingest_operation.spawn(request)

    return {"call_id": call.object_id}


@web_app.post("/crawl")
async def crawl(
    request: CrawlRequest,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        return _not_available_in_region("crawl")

    call = crawl_operation.spawn(request)
    return {"call_id": call.object_id}


@web_app.post("/youtube")
async def youtube(
    request: YouTubeRequest,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        return _not_available_in_region("youtube")

    call = youtube_operation.spawn(request)
    return {"call_id": call.object_id}


@web_app.get("/ingest/results/{call_id}")
async def poll_ingest_results(
    call_id: str,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        return _not_found()

    function_call = modal.FunctionCall.from_id(call_id)
    try:
        return function_call.get(timeout=0)
    except TimeoutError:
        http_accepted_code = 202
        return JSONResponse({}, status_code=http_accepted_code)


@web_app.get("/crawl/results/{call_id}")
async def poll_crawl_results(
    call_id: str,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        return _not_found()

    function_call = modal.FunctionCall.from_id(call_id)
    try:
        return function_call.get(timeout=0)
    except TimeoutError:
        http_accepted_code = 202
        return JSONResponse({}, status_code=http_accepted_code)


@web_app.get("/youtube/results/{call_id}")
async def poll_youtube_results(
    call_id: str,
    api_key: Annotated[str | None, Header(alias="api-key")] = None,
):
    if api_key != os.getenv("AGENTSET_API_KEY"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": status.HTTP_401_UNAUTHORIZED,
                "message": "api-key is not valid!",
            },
        )

    if is_eu():
        return _not_found()

    function_call = modal.FunctionCall.from_id(call_id)
    try:
        return function_call.get(timeout=0)
    except TimeoutError:
        http_accepted_code = 202
        return JSONResponse({}, status_code=http_accepted_code)
