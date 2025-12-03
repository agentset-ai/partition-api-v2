import os
import time
from datalab_sdk.models import ConversionResult
import requests
from requests.adapters import HTTPAdapter, Retry
from .s3 import upload_image_to_r2
from .schema import ParseDocumentResult, ParseOptions
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

    markdown = result.markdown
    if markdown is not None:
        markdown = re.sub(_PAGE_DELIMITER, _PAGE_DELIMITER_STRING, markdown)
        markdown = re.sub(_img_pattern, _img_replacement, markdown)

    # Upload images to R2 and replace their filenames with uploaded URLs in the markdown
    if result.images and isinstance(result.images, dict):
        image_url_mapping = {}

        # result.images is a dict: {filename: base64_content}
        for image_filename, base64_content in result.images.items():
            if image_filename and base64_content:
                # Upload to R2 and get the public URL
                uploaded_url = upload_image_to_r2(
                    image_filename, base64_content, namespace_id, document_id
                )
                image_url_mapping[image_filename] = uploaded_url
                print(f"Uploaded image {image_filename} to {uploaded_url}")

        # Replace image filenames with their R2 URLs in the markdown
        for original_filename, uploaded_url in image_url_mapping.items():
            # Replace markdown image references: ![alt](filename) -> ![alt](uploaded_url)
            markdown = markdown.replace(f"]({original_filename})", f"]({uploaded_url})")
            markdown = markdown.replace(f"({original_filename})", f"({uploaded_url})")

    pages: list[str] = re.split(_PAGE_DELIMITER_STRING, markdown)
    content_by_page: list[dict] = []

    # skip the first page because it'll always be empty
    for idx, page in enumerate(pages[1:]):
        stripped_page = page.strip()
        # check if the trimmed page is empty, if it is, skip it
        if len(stripped_page) == 0:
            continue

        content_by_page.append({"text": stripped_page, "page": idx + 1})

    return ParseDocumentResult(pages=content_by_page, page_count=result.page_count)


def parse_document(
    file_url: str, options: ParseOptions, namespace_id: str, document_id: str
):
    url = "https://www.datalab.to/api/v1/marker"
    form_data = {
        "force_ocr": (None, options.force_ocr),
        "format_lines": (None, options.format_lines),
        "strip_existing_ocr": (None, options.strip_existing_ocr),
        "disable_image_extraction": (None, options.disable_image_extraction),
        "disable_ocr_math": (None, options.disable_ocr_math),
        "use_llm": (None, options.use_llm),
        "mode": (None, options.mode),
        "block_correction_prompt": (None, options.block_correction_prompt),
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

    response = _session.post(url, files=form_data, headers=_headers)
    data = response.json()

    if not "success" in data or data["success"] == False:
        raise Exception("Could not parse document")

    return _get_markdown_from_job(data["request_id"], namespace_id, document_id)
