from fastapi import status
from .app import app, REGION
from .notify_trigger import notify_workflow
from .datalab_parser import (
    parse_document,
    parse_uploaded_document,
    uses_datalab_file_upload,
    DATALAB_SUPPORTED_MIME_TYPES,
    DATALAB_SUPPORTED_EXTENSIONS,
    PAGED_MIME_TYPES,
    PAGED_EXTENSIONS,
)
from .schema import IngestRequest
from .file_type import extract_file_from_request
from .chunker import chunk_documents
from .s3 import upload_chunks_to_r2
from .redis_client import get_redis
from .csv_parser import parse_csv
from .jobs import delete_job, load_job
from .region import check_eu_config, function_options, is_eu, log_error
from .errors import eu_error_body
import uuid
import json

# chunk batches are deleted by the app once embedded; on EU they also expire
EU_BATCH_TTL_SECONDS = 3 * 24 * 60 * 60  # 3 days


@app.function(timeout=7200, **function_options(REGION))  # 2 hours
def ingest_operation(request: IngestRequest | str):
    # on EU the web endpoint spawns this function with a job id
    if is_eu():
        return _run_eu_job(request)

    print("Ingest Operation:")
    print(request.model_dump_json(indent=2))
    return _ingest(request)


def _run_eu_job(job_id: str):
    if not check_eu_config():
        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR}

    request: IngestRequest | None = None
    try:
        request = load_job(job_id, IngestRequest)
        if request is None:
            print(f"Ingest Operation: job not found job_id={job_id}")
            return None

        print(
            f"Ingest Operation: job_id={job_id} namespace_id={request.namespace_id} document_id={request.document_id}"
        )
        return _ingest(request)
    except Exception as e:
        ids = {
            "job_id": job_id,
            "namespace_id": request.namespace_id if request else None,
            "document_id": request.document_id if request else None,
        }
        log_error("Ingest Operation failed", e, **ids)
        if request is not None:
            _notify_failure(request, **ids)
        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR}
    finally:
        # kept until the job has run, so a retried input can still load it
        if request is not None:
            try:
                delete_job(job_id)
            except Exception as e:
                log_error("Failed to delete ingest job", e, job_id=job_id)


def _notify_failure(request: IngestRequest, **ids: str | None):
    """Best-effort: completes the waitpoint with a generic error."""
    try:
        notify_workflow(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            body={"message": "Failed to process document", "code": "processing_failed"},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
    except Exception as e:
        log_error("Failed to notify the workflow", e, **ids)


def _ingest(request: IngestRequest):
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
        if is_eu():
            log_error(
                "Failed to download file",
                e,
                namespace_id=request.namespace_id,
                document_id=request.document_id,
            )
            body = {"message": "Failed to download file", "code": "download_failed"}
        else:
            body = {"message": f"Failed to download file: {str(e)}"}

        return notify_workflow(
            status=status.HTTP_400_BAD_REQUEST,
            body=body,
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
            if uses_datalab_file_upload():
                result = parse_uploaded_document(
                    file=payload,
                    options=request.parse_options,
                    namespace_id=request.namespace_id,
                    document_id=request.document_id,
                )
            else:
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
        elif payload.mime_type in [
            "text/plain",
            "text/markdown",
        ] or payload.extension in ["txt", "md"]:
            documents = [
                {
                    "text": payload.file.getvalue().decode("utf-8", errors="ignore"),
                    "page": None,
                }
            ]
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

        if is_eu():
            # the app already has the filename; keep it out of the completion data
            del result["metadata"]["filename"]

        if total_pages is not None:
            result["total_pages"] = total_pages

        redis_client = get_redis()
        batch_ttl = {"ex": EU_BATCH_TTL_SECONDS} if is_eu() else {}

        # Store each batch in Redis with the specified key format
        for batch_idx, batch in enumerate(batches):
            redis_key = batch_template.replace("[BATCH_INDEX]", str(batch_idx))
            redis_client.set(redis_key, json.dumps(batch), **batch_ttl)

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
        if is_eu():
            log_error(
                "Failed to process document",
                e,
                namespace_id=request.namespace_id,
                document_id=request.document_id,
            )
            body = eu_error_body(e, "processing_failed", "Failed to process document")
        else:
            import traceback

            traceback.print_exc()
            body = {"message": str(e)}

        return notify_workflow(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            body=body,
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
