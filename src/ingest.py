import os
from fastapi import status
from .app import app
from .notify_trigger import notify_workflow
from .datalab_parser import (
    parse_document,
    DATALAB_SUPPORTED_MIME_TYPES,
    DATALAB_SUPPORTED_EXTENSIONS,
    PAGED_MIME_TYPES,
    PAGED_EXTENSIONS,
)
from .schema import IngestRequest
from .file_type import extract_file_from_request
from .chunker import chunk_documents
from .s3 import upload_chunks_to_r2
from redis import Redis
from .csv_parser import parse_csv
import uuid
import json


@app.function(timeout=7200)  # 2 hours
def ingest_operation(request: IngestRequest):
    print("Ingest Operation:")
    print(request.model_dump_json(indent=2))

    # Count how many input sources are provided
    input_sources = sum(1 for x in [request.url, request.text] if x is not None)
    if input_sources != 1:
        return notify_workflow(
            status=status.HTTP_400_BAD_REQUEST,
            body={"message": "Only one of url or text can be provided"},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )

    try:
        payload = extract_file_from_request(request)
    except Exception as e:
        return notify_workflow(
            status=status.HTTP_400_BAD_REQUEST,
            body={"message": f"Failed to download file: {str(e)}"},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )

    try:
        documents = []  # {page:int, text: str}
        total_pages = None

        # if the user passed a file url, and the file is a supported mime type, parse it with datalab
        if request.url and (
            (payload.mime_type in DATALAB_SUPPORTED_MIME_TYPES)
            or (payload.extension in DATALAB_SUPPORTED_EXTENSIONS)
        ):
            result = parse_document(
                file_url=request.url,
                options=request.parse_options,
                namespace_id=request.namespace_id,
                document_id=request.document_id,
            )
            documents = result.pages
            if (
                payload.mime_type in PAGED_MIME_TYPES
                or payload.extension in PAGED_EXTENSIONS
            ):
                total_pages = result.page_count
        elif payload.mime_type in [
            "text/csv",
            "text/tab-separated-values",
        ] or payload.extension in ["csv", "tsv"]:
            result = parse_csv(payload)
            documents = [{"text": result, "page": None}]
        else:
            from markitdown import MarkItDown, StreamInfo

            md = MarkItDown(enable_plugins=False)  # Set to True to enable plugins
            result = md.convert(
                source=payload.file,
                stream_info=StreamInfo(
                    filename=payload.file_name,
                    mimetype=payload.mime_type,
                    extension=payload.extension,
                ),
            )
            documents = [{"text": result.markdown, "page": None}]

        if len(documents) == 0:
            return notify_workflow(
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                body={"message": "couldn't parse document"},
                trigger_token_id=request.trigger_token_id,
                trigger_access_token=request.trigger_access_token,
            )

        batches, total_characters, total_chunks, total_batches = chunk_documents(
            documents,
            batch_size=request.batch_size,
            chunk_options=request.chunk_options,
        )

        results_id = str(uuid.uuid4())
        batch_template = f"results_{results_id}_[BATCH_INDEX]"
        result = {
            "metadata": {
                "filename": request.filename,
                "filetype": payload.mime_type,
                "size_in_bytes": payload.size_in_bytes,
            },
            "total_characters": total_characters,
            "total_chunks": total_chunks,
            "total_batches": total_batches,
            "results_id": results_id,
            "batch_template": batch_template,
        }

        if total_pages is not None:
            result["total_pages"] = total_pages

        redis_client = Redis(
            host=os.getenv("REDIS_HOST"),
            port=os.getenv("REDIS_PORT"),
            password=os.getenv("REDIS_PASSWORD"),
            ssl=True,
        )

        # Store each batch in Redis with the specified key format
        for batch_idx, batch in enumerate(batches):
            redis_key = batch_template.replace("[BATCH_INDEX]", str(batch_idx))
            redis_client.set(redis_key, json.dumps(batch))

        # upload the result
        upload_chunks_to_r2(
            namespace_id=request.namespace_id,
            document_id=request.document_id,
            data={
                "metadata": request.extra_metadata or {},
                "total_chunks": total_chunks,
                "total_characters": total_characters,
                # flatten batches
                "chunks": [item for batch in batches for item in batch],
            },
        )

        return notify_workflow(
            status=status.HTTP_200_OK,
            body=result,
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return notify_workflow(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            body={"message": str(e)},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
