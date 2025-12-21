from pydantic import BaseModel
from typing import Literal


class ParseOptions(BaseModel):
    mode: Literal["fast", "balanced", "accurate"] = "balanced"

    # Disable image extraction from the PDF.
    disable_image_extraction: bool = False

    # Disable synthetic image captions/descriptions in output. Images will be rendered as plain img tags without alt text or the img-description wrapper div.
    disable_image_captions: bool = False

    # Additional configuration options for marker. This should be a JSON string with key-value pairs. For example, '{"key": "value"}'. This supports these keys: 'keep_pageheader_in_output' (bool), 'keep_pagefooter_in_output' (bool), 'keep_spreadsheet_formatting' (bool)
    additional_config: dict | None = None

    # Comma-separated list of extras to enable. Currently supports: 'track_changes', 'chart_understanding', 'extract_links'.
    extras: str | None = None


class ChunkOptions(BaseModel):
    chunk_size: int = 2048
    # if available, we'll split the text using delimiters before chunking to ensure they don't overlap
    delimiter: str | None = None
    language_code: (
        Literal[
            "af",
            "am",
            "ar",
            "bg",
            "bn",
            "ca",
            "cs",
            "cy",
            "da",
            "de",
            "en",
            "es",
            "et",
            "fa",
            "fi",
            "fr",
            "ga",
            "gl",
            "he",
            "hi",
            "hr",
            "hu",
            "id",
            "is",
            "it",
            "jp",
            "kr",
            "lt",
            "lv",
            "mk",
            "ms",
            "mt",
            "ne",
            "nl",
            "no",
            "pl",
            "pt",
            "pt-BR",
            "ro",
            "ru",
            "sk",
            "sl",
            "sr",
            "sv",
            "sw",
            "ta",
            "te",
            "th",
            "tl",
            "tr",
            "uk",
            "ur",
            "vi",
            "zh",
            "zu",
        ]
        | None
    ) = None


class IngestRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    filename: str | None = None
    extra_metadata: dict | None = None
    parse_options: ParseOptions = ParseOptions()
    chunk_options: ChunkOptions = ChunkOptions()
    batch_size: int = 5

    trigger_token_id: str
    trigger_access_token: str

    # Namespace and document identifiers for organizing uploaded images
    namespace_id: str
    document_id: str


class CrawlOptions(BaseModel):
    max_depth: int = 5
    limit: int = 50
    exclude_paths: list[str] | None = None
    include_paths: list[str] | None = None

    include_selectors: list[str] | None = None
    exclude_selectors: list[str] | None = None
    only_main_content: bool = True

    headers: dict[str, str] | None = None


class CrawlRequest(BaseModel):
    url: str
    extra_metadata: dict | None = None

    chunk_options: ChunkOptions = ChunkOptions()
    crawl_options: CrawlOptions = CrawlOptions()

    trigger_token_id: str
    trigger_access_token: str

    namespace_id: str


class YouTubeRequest(BaseModel):
    urls: list[str]  # video, playlist, or channel URLs
    extra_metadata: dict | None = None

    transcript_languages: list[str] | None = None  # preferred transcript languages
    include_metadata: bool = False  # whether to include video metadata in the markdown

    chunk_options: ChunkOptions = ChunkOptions()

    trigger_token_id: str
    trigger_access_token: str

    namespace_id: str


class ParseDocumentResult(BaseModel):
    pages: list[dict]
    page_count: int
