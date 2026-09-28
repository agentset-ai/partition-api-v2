import os
import time
from datalab_sdk.models import ConversionResult
import requests
from requests.adapters import HTTPAdapter, Retry
from .s3 import upload_image_to_r2
from .schema import ParseDocumentResult, ParseOptions
from .file_type import ExtractedFile, normalize_mime_type
from .region import is_eu, log_error
from .errors import PartitionError
import re
import json

_max_polls = 600
_headers = {"X-API-Key": os.getenv("DATALAB_API_KEY")}
_session = requests.Session()
_retries = Retry(
    total=20,
    backoff_factor=4,
    status_forcelist=[429],
    allowed_methods=["GET", "POST"],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retries)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

_PAGE_DELIMITER = re.compile(r"\n\n\{\d+\}-{48}\n\n")
_PAGE_DELIMITER_STRING = "___AGENTSET_PAGE_DELIMITER___"
_img_pattern = r"!\[([^\]]+)\]\(\s*\)\s*!\[\s*\]\(\s*([^)]+?)\s*\)"
_img_replacement = r"![\1](\2)"

DATALAB_SUPPORTED_MIME_TYPES = [
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
    # "text/html",
    "application/epub+zip",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/tiff",
    "image/jpg",
]

# for these mime types, we'll bill by pages not characters
# pdf, doc, docx, ppt, pptx, odp
PAGED_MIME_TYPES = [
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
    "application/epub+zip",
]

PAGED_EXTENSIONS = [
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "odp",
    "epub",
]

DATALAB_SUPPORTED_EXTENSIONS = [
    "pdf",
    "xls",
    "xlsx",
    "ods",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "odp",
    "epub",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".tiff",
    ".tif",
    ".webp",
]


def _get_and_wait_for_job(job_id: str):
    job: dict | None = None
    url = f"https://www.datalab.to/api/v1/marker/{job_id}"

    for i in range(_max_polls):
        response = _session.get(url, headers=_headers)
        job = response.json()
        if job["status"] != "processing":
            break

        print(f"Job {job_id} is pending, checking again in 5 seconds")
        time.sleep(5)  # wait for 5 seconds before checking again

    if job["success"] == False or job["status"] == "failed":
        raise Exception(job["error"] if "error" in job else "Job failed")

    return ConversionResult(
        success=job.get("success", False),
        output_format=job.get("output_format"),
        markdown=job.get("markdown"),
        html=job.get("html"),
        json=job.get("json"),
        chunks=job.get("chunks"),
        extraction_schema_json=job.get("extraction_schema_json"),
        images=job.get("images"),
        metadata=job.get("metadata"),
        error=job.get("error"),
        page_count=job.get("page_count"),
        status=job.get("status", "complete"),
    )


def _get_markdown_from_job(
    job_id: str, namespace_id: str, document_id: str
) -> ParseDocumentResult:
    result = _get_and_wait_for_job(job_id)
    return _build_parse_result(
        markdown=result.markdown,
        images=result.images,
        page_count=result.page_count,
        namespace_id=namespace_id,
        document_id=document_id,
    )


def _build_parse_result(
    markdown: str | None,
    images: dict | None,
    page_count: int | None,
    namespace_id: str,
    document_id: str,
) -> ParseDocumentResult:
    if markdown is not None:
        markdown = re.sub(_PAGE_DELIMITER, _PAGE_DELIMITER_STRING, markdown)
        markdown = re.sub(_img_pattern, _img_replacement, markdown)

    # Upload images to R2 and replace their filenames with uploaded URLs in the markdown
    if images and isinstance(images, dict):
        image_url_mapping = {}

        # images is a dict: {filename: base64_content}
        for image_filename, base64_content in images.items():
            if image_filename and base64_content:
                # Upload to R2 and get the public URL
                uploaded_url = upload_image_to_r2(
                    image_filename, base64_content, namespace_id, document_id
                )
                image_url_mapping[image_filename] = uploaded_url
                if not is_eu():
                    print(f"Uploaded image {image_filename} to {uploaded_url}")

        # Replace image filenames with their R2 URLs in the markdown
        for original_filename, uploaded_url in image_url_mapping.items():
            # Replace markdown image references: ![alt](filename) -> ![alt](uploaded_url)
            markdown = markdown.replace(f"]({original_filename})", f"]({uploaded_url})")
            markdown = markdown.replace(f"({original_filename})", f"({uploaded_url})")

    pages: list[str] = re.split(_PAGE_DELIMITER_STRING, markdown)
    content_by_page: list[dict] = []

    # when page delimiters exist, the first element is empty (content before first delimiter)
    start_idx = 1 if len(pages) > 1 else 0
    for idx, page in enumerate(pages[start_idx:]):
        stripped_page = page.strip()
        # check if the trimmed page is empty, if it is, skip it
        if len(stripped_page) == 0:
            continue

        content_by_page.append({"text": stripped_page, "page": idx + 1})

    return ParseDocumentResult(pages=content_by_page, page_count=page_count)


def _form_data(file_url: str, options: ParseOptions) -> dict:
    return {
        "disable_image_extraction": (None, options.disable_image_extraction),
        "disable_image_captions": (None, options.disable_image_captions),
        "extras": (None, options.extras),
        "mode": (None, options.mode),
        "additional_config": (
            None,
            (
                json.dumps(options.additional_config)
                if options.additional_config
                else None
            ),
        ),
        # we won't allow customizing these
        "file_url": (None, file_url),
        "output_format": (None, "markdown"),
        "paginate": (None, True),
    }


def parse_document(
    file_url: str, options: ParseOptions, namespace_id: str, document_id: str
):
    url = "https://www.datalab.to/api/v1/marker"
    form_data = _form_data(file_url, options)

    response = _session.post(url, files=form_data, headers=_headers)
    data = response.json()

    if not "success" in data or data["success"] == False:
        raise Exception("Could not parse document")

    return _get_markdown_from_job(data["request_id"], namespace_id, document_id)


# EU: documents are uploaded to Datalab's EU storage and processed there, so
# Datalab never receives a URL to our storage
_DATALAB_API_URL = "https://www.datalab.to/api/v1"
DATALAB_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB
_API_TIMEOUT = 60
_TRANSFER_TIMEOUT = (30, 600)

_EXTENSION_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "tiff": "image/tiff",
    "tif": "image/tiff",
}
_CONTENT_TYPE_EXTENSIONS = {
    **{
        content_type: extension
        for extension, content_type in reversed(_EXTENSION_CONTENT_TYPES.items())
    },
    "image/jpg": "jpg",
}


def uses_datalab_file_upload() -> bool:
    if os.getenv("DATALAB_PROCESSING_LOCATION") == "eu":
        return True

    if is_eu():
        raise PartitionError(
            "parser_not_configured",
            "Document parsing is not configured for this region",
        )

    return False


def _upload_file_info(file: ExtractedFile, document_id: str) -> tuple[str, str]:
    """Returns the filename and content type to register with Datalab.

    The filename is the document id plus its extension, not the original name.
    """
    mime_type = normalize_mime_type(file.mime_type or "")
    extension = (file.extension or "").lower()
    if extension not in _EXTENSION_CONTENT_TYPES:
        extension = _CONTENT_TYPE_EXTENSIONS.get(mime_type, "bin")

    content_type = (
        mime_type
        if mime_type in DATALAB_SUPPORTED_MIME_TYPES
        else _EXTENSION_CONTENT_TYPES.get(extension, "application/octet-stream")
    )
    return f"{document_id}.{extension}", content_type


def _merge_result(downloaded: dict, polled: dict) -> dict:
    # content fields in the poll response are empty when the result is downloaded;
    # other non-empty poll fields (e.g. page_count) take precedence
    return {
        **downloaded,
        **{k: v for k, v in polled.items() if v not in (None, "", [], {})},
    }


def _wait_for_conversion(data: dict) -> dict:
    request_id = data["request_id"]
    check_url = data.get("request_check_url") or ""
    if not check_url.startswith(f"{_DATALAB_API_URL}/"):
        check_url = f"{_DATALAB_API_URL}/convert/{request_id}"

    job: dict = {}
    for _ in range(_max_polls):
        response = _session.get(check_url, headers=_headers, timeout=_API_TIMEOUT)
        job = response.json()
        if job.get("status") != "processing":
            break

        print(f"Job {request_id} is pending, checking again in 5 seconds")
        time.sleep(5)
    else:
        raise PartitionError("parse_timeout", "Document parsing timed out")

    if job.get("success") is False or job.get("status") == "failed":
        raise PartitionError("parse_failed", "Could not parse document")

    result_url = job.get("result_url")
    if result_url:
        # signed link: fetched without the API key and never logged
        response = _session.get(result_url, timeout=_TRANSFER_TIMEOUT)
        response.raise_for_status()
        job = _merge_result(response.json(), job)

    return job


def _delete_file(file_id: int | str) -> None:
    try:
        response = _session.delete(
            f"{_DATALAB_API_URL}/files/{file_id}",
            headers=_headers,
            timeout=_API_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as e:
        log_error("Failed to delete Datalab file", e, file_id=file_id)


def parse_uploaded_document(
    file: ExtractedFile, options: ParseOptions, namespace_id: str, document_id: str
) -> ParseDocumentResult:
    if file.size_in_bytes > DATALAB_MAX_FILE_BYTES:
        raise PartitionError(
            "file_too_large", "File exceeds the 200 MB limit for document parsing"
        )

    processing_location = os.getenv("DATALAB_PROCESSING_LOCATION")
    filename, content_type = _upload_file_info(file, document_id)
    file_id = None

    try:
        response = _session.post(
            f"{_DATALAB_API_URL}/files/upload",
            json={
                "filename": filename,
                "content_type": content_type,
                "processing_location": processing_location,
            },
            headers=_headers,
            timeout=_API_TIMEOUT,
        )
        response.raise_for_status()
        upload = response.json()
        file_id = upload["file_id"]

        # presigned upload URL: no API key
        response = _session.put(
            upload["upload_url"],
            data=file.file.getvalue(),
            headers={"Content-Type": content_type},
            timeout=_TRANSFER_TIMEOUT,
        )
        response.raise_for_status()

        response = _session.get(
            f"{_DATALAB_API_URL}/files/{file_id}/confirm",
            headers=_headers,
            timeout=_API_TIMEOUT,
        )
        response.raise_for_status()
        if response.json().get("success") is False:
            raise PartitionError(
                "upload_failed", "Could not upload document for parsing"
            )

        # Datalab rejects multipart requests when processing_location is set,
        # so the fields are sent url-encoded
        form_data = {
            key: value
            for key, (_, value) in _form_data(upload["reference"], options).items()
        }
        form_data["processing_location"] = processing_location
        response = _session.post(
            f"{_DATALAB_API_URL}/convert",
            data=form_data,
            headers=_headers,
            timeout=_API_TIMEOUT,
        )
        data = response.json()
        if not data.get("success"):
            raise PartitionError("parse_failed", "Could not parse document")

        job = _wait_for_conversion(data)
        if job.get("markdown") is None:
            raise PartitionError("parse_failed", "Could not parse document")

        return _build_parse_result(
            markdown=job["markdown"],
            images=job.get("images"),
            page_count=job.get("page_count"),
            namespace_id=namespace_id,
            document_id=document_id,
        )
    finally:
        if file_id is not None:
            _delete_file(file_id)
