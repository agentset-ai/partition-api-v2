from email.message import Message
from requests.structures import CaseInsensitiveDict


def extract_filename_from_headers(headers: CaseInsensitiveDict) -> str | None:
    content_disposition = headers.get("Content-Disposition")
    if not content_disposition:
        return None

    msg = Message()
    msg["content-disposition"] = content_disposition
    return msg.get_filename()
