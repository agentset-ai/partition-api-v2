import magic
from io import BytesIO
from .schema import IngestRequest
import requests
from .filename import extract_filename_from_headers
from dataclasses import dataclass
from typing import Optional


def detect_mimetype(file: BytesIO) -> str:
    file_head = file.read(8192)
    mime_type = magic.from_buffer(file_head, mime=True)
    return mime_type.lower()


@dataclass
class ExtractedFile:
    file: BytesIO
    mime_type: str
    size_in_bytes: int
    file_name: Optional[str] = None
    extension: Optional[str] = None
    url: Optional[str] = None


def extract_file_from_request(request: IngestRequest) -> ExtractedFile:
    file_stream = None
    size_in_bytes = 0
    file_name = None
    extension = None
    url = None
    mime_type = None

    if request.filename:
        file_name = request.filename

    if request.url:
        url = request.url
        response = requests.get(request.url)
        response.raise_for_status()
        size_in_bytes = len(response.content)
        file_stream = BytesIO(response.content)
        if not file_name:
            file_name = extract_filename_from_headers(response.headers)
        mime_type = response.headers.get("Content-Type")
    else:
        text_bytes = request.text.encode("utf-8")
        size_in_bytes = len(text_bytes)
        file_stream = BytesIO(text_bytes)
        mime_type = "text/plain"
        extension = "txt"

    if file_name:
        extension = file_name.split(".")[-1]

    return ExtractedFile(
        file=file_stream,
        # don't override the mime type if it's already set
        mime_type=mime_type or detect_mimetype(file_stream),
        size_in_bytes=size_in_bytes,
        file_name=file_name,
        extension=extension,
        url=url,
    )
