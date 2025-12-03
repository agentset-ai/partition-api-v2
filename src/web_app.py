from typing import Annotated
from fastapi import FastAPI, Header, status
from fastapi.responses import JSONResponse
import modal
import os

from .schema import IngestRequest, CrawlRequest, YouTubeRequest
from .ingest import ingest_operation
from .crawl import crawl_operation
from .yt import youtube_operation
from .app import app

web_app = FastAPI()


@app.function()
@modal.asgi_app()
def partition_api():
    return web_app


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

    function_call = modal.FunctionCall.from_id(call_id)
    try:
        return function_call.get(timeout=0)
    except TimeoutError:
        http_accepted_code = 202
        return JSONResponse({}, status_code=http_accepted_code)
