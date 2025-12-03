from pydantic import BaseModel
from typing import Literal


class ParseOptions(BaseModel):
    force_ocr: bool = False

    # detect math and styles
    format_lines: bool = False

    # Strip existing OCR text from the PDF and re-run OCR. If force_ocr is set, this will be ignored.
    strip_existing_ocr: bool = False

    # Disable image extraction from the PDF. If use_llm is also set, then images will be automatically captioned.
    disable_image_extraction: bool = False

    # Disable inline math recognition in OCR.
    disable_ocr_math: bool = False

    # Significantly improves accuracy by using an LLM to enhance tables, forms, inline math, and layout detection. Will increase latency.
    use_llm: bool = True

    mode: Literal["fast", "balanced", "accurate"] = "balanced"

    # A custom prompt to use for block correction.
    block_correction_prompt: str | None = None

    # Additional configuration options for marker. This should be a JSON string with key-value pairs. For example, '{"key": "value"}'. This supports these keys: ['disable_links', 'keep_pageheader_in_output', 'keep_pagefooter_in_output', 'filter_blank_pages', 'drop_repeated_text', 'layout_coverage_threshold', 'merge_threshold', 'height_tolerance', 'gap_threshold', 'image_threshold', 'min_line_length', 'level_count', 'default_level', 'no_merge_tables_across_pages', 'force_layout_block']
    additional_config: dict | None = None


class ChunkOptions(BaseModel):
    chunk_size: int = 2048
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
