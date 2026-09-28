import os
from fastapi import status
from .app import app, REGION
from .region import check_eu_config, function_options, is_eu, log_error
from .errors import eu_error_body
from .jobs import delete_job, load_job
from .notify_trigger import notify_workflow
from .schema import CrawlRequest
from .chunker import chunk_documents, langs
from firecrawl import Firecrawl
from typing import Callable
from cuid2 import cuid_wrapper
from .s3 import upload_chunks_to_r2


@app.function(timeout=7200, **function_options(REGION))  # 2 hours
def crawl_operation(request: CrawlRequest | str):
    # on EU the web endpoint spawns this function with a job id
    if is_eu():
        return _run_eu_job(request)

    print("Crawl Operation:")
    print(request.model_dump_json(indent=2))
    return _crawl(request)


def _run_eu_job(job_id: str):
    if not check_eu_config():
        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR}

    request: CrawlRequest | None = None
    try:
        request = load_job(job_id, CrawlRequest)
        if request is None:
            print(f"Crawl Operation: job not found job_id={job_id}")
            return None

        print(
            f"Crawl Operation: job_id={job_id} namespace_id={request.namespace_id}"
        )
        return _crawl(request)
    except Exception as e:
        ids = {
            "job_id": job_id,
            "namespace_id": request.namespace_id if request else None,
        }
        log_error("Crawl Operation failed", e, **ids)
        if request is not None:
            try:
                notify_workflow(
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    body={"message": "Failed to crawl", "code": "crawl_failed"},
                    trigger_token_id=request.trigger_token_id,
                    trigger_access_token=request.trigger_access_token,
                )
            except Exception as notify_error:
                log_error("Failed to notify the workflow", notify_error, **ids)
        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR}
    finally:
        # kept until the job has run, so a retried input can still load it
        if request is not None:
            try:
                delete_job(job_id)
            except Exception as e:
                log_error("Failed to delete crawl job", e, job_id=job_id)


def _crawl(request: CrawlRequest):
    firecrawl = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY"))
    cuid_generator: Callable[[], str] = cuid_wrapper()

    try:
        scrape_options = {
            "formats": ["markdown"],
        }
        if request.crawl_options.only_main_content is not None:
            scrape_options["onlyMainContent"] = request.crawl_options.only_main_content
        if request.crawl_options.include_selectors is not None:
            scrape_options["includeTags"] = request.crawl_options.include_selectors
        if request.crawl_options.exclude_selectors is not None:
            scrape_options["excludeTags"] = request.crawl_options.exclude_selectors
        if request.crawl_options.headers is not None:
            scrape_options["headers"] = request.crawl_options.headers

        crawl_job = firecrawl.crawl(
            request.url,
            limit=request.crawl_options.limit,
            max_discovery_depth=request.crawl_options.max_depth,
            exclude_paths=request.crawl_options.exclude_paths,
            include_paths=request.crawl_options.include_paths,
            scrape_options=scrape_options,
            poll_interval=5,
            timeout=1800,  # 30 minutes
        )

        if (
            crawl_job.status == "failed"
            or crawl_job.status == "cancelled"
            or len(crawl_job.data) == 0
        ):
            return notify_workflow(
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                body={"message": "crawl failed"},
                trigger_token_id=request.trigger_token_id,
                trigger_access_token=request.trigger_access_token,
            )

        documents = []
        for document in crawl_job.data:
            metadata = document.metadata
            url = metadata.source_url if metadata.source_url is not None else None
            title = metadata.title if metadata.title is not None else None
            description = (
                metadata.description if metadata.description is not None else None
            )
            language = metadata.language if metadata.language is not None else None

            if language is not None and language in langs:
                request.chunk_options.language_code = language

            chunks, total_characters, total_chunks, _b = chunk_documents(
                documents=[{"text": document.markdown}],
                batch_size=None,
                chunk_options=request.chunk_options,
            )

            documents.append(
                {
                    "id": cuid_generator(),
                    "chunks": chunks,
                    "url": url,
                    "metadata": {
                        "title": title,
                        "description": description,
                        "language": language,
                    },
                    "total_bytes": len(document.markdown),
                    "total_characters": total_characters,
                    "total_chunks": total_chunks,
                }
            )

        for doc in documents:
            metadata = {
                **(request.extra_metadata or {}),
                "namespaceId": request.namespace_id,
                "documentId": doc["id"],
            }

            if doc["url"]:
                metadata["page_url"] = doc["url"]

            page_metadata = doc["metadata"]
            if page_metadata["title"]:
                metadata["page_title"] = page_metadata["title"]
            if page_metadata["description"]:
                metadata["page_description"] = page_metadata["description"]
            if page_metadata["language"]:
                metadata["page_language"] = page_metadata["language"]

            upload_chunks_to_r2(
                namespace_id=request.namespace_id,
                document_id=doc["id"],
                data={
                    "metadata": metadata,
                    "total_chunks": doc["total_chunks"],
                    "total_characters": doc["total_characters"],
                    "chunks": doc["chunks"],
                },
            )

            # once it's done, delete fields that we don't want to send to the user
            del doc["chunks"]
            del doc["metadata"]

        return notify_workflow(
            status=status.HTTP_200_OK,
            body={"documents": documents},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
    except Exception as e:
        if is_eu():
            log_error("Failed to crawl", e, namespace_id=request.namespace_id)
            body = eu_error_body(e, "crawl_failed", "Failed to crawl")
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
