from chonkie import (
    TableChunker,
    CodeChunker,
    RecursiveChunker,
    MarkdownChef,
    RecursiveRules,
)
from chonkie.types import MarkdownImage, MarkdownTable, MarkdownCode
from typing import List
from .schema import ChunkOptions
import uuid


class CustomMarkdownChef(MarkdownChef):
    # return an empty list, we want to disable image extraction
    def extract_images(self, markdown: str) -> List[MarkdownImage]:
        return []


chef = CustomMarkdownChef()

langs = [
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


def parse_markdown(markdown: str, chunk_options: ChunkOptions):
    chunk_size = chunk_options.chunk_size
    language = chunk_options.language_code
    rules = (
        RecursiveRules.from_recipe(
            lang=language,
        )
        if (language is not None and language in langs)
        else RecursiveRules()
    )

    recursive_chunker: RecursiveChunker | None = None
    code_recursive_chunker: RecursiveChunker | None = None
    table_chunker: TableChunker | None = None
    code_chunker: CodeChunker | None = None

    document = chef.parse(markdown + "\n")

    # each item in the objects above has a start_index, we need to:
    # 1) re-order them by start_index -> items
    # 2) loop over them, and for each one, use an appropriate chunker to chunk it
    # 3) add the chunks to the final_chunks list
    items = [
        *[table for table in document.tables],
        *[code for code in document.code],
        *[image for image in document.images],
        *[chunk for chunk in document.chunks],
    ]

    items.sort(key=lambda x: x.start_index)

    raw_chunks: list[str] = []

    for item in items:
        chunks = []
        if isinstance(item, MarkdownTable):
            if table_chunker is None:
                table_chunker = TableChunker(chunk_size=chunk_size)

            chunks = table_chunker.chunk(item.content)
        elif isinstance(item, MarkdownCode):
            try:
                if code_chunker is None or (
                    item.language is not None and code_chunker.language != item.language
                ):
                    code_chunker = CodeChunker(
                        language="auto" if item.language is None else item.language,
                        chunk_size=chunk_size,
                    )
                chunks = code_chunker.chunk(item.content)
            except Exception as e:
                # chunk as text via recursive chunker
                if code_recursive_chunker is None:
                    code_recursive_chunker = RecursiveChunker(
                        chunk_size=chunk_size, rules=RecursiveRules()
                    )
                chunks = code_recursive_chunker.chunk(item.content)
        else:
            if recursive_chunker is None:
                recursive_chunker = RecursiveChunker(chunk_size=chunk_size, rules=rules)

            chunks = recursive_chunker.chunk(item.text)

        for chunk in chunks:
            text = chunk.text.strip()
            if not text:
                continue
            raw_chunks.append(text)

    # merge consecutive small chunks that fit together
    final_chunks: list[dict] = []
    sequence_number = 0

    for text in raw_chunks:
        if final_chunks and len(final_chunks[-1]["text"]) + len(text) + 2 <= chunk_size:
            # merge with previous chunk
            final_chunks[-1]["text"] += "\n\n" + text
        else:
            # check if previous chunk ends with a heading - move it to current
            if final_chunks:
                prev_text = final_chunks[-1]["text"]
                lines = prev_text.split("\n")
                # find trailing heading lines
                heading_start = len(lines)
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].lstrip().startswith("#"):
                        heading_start = i
                    elif lines[i].strip():
                        break

                if heading_start < len(lines):
                    heading = "\n".join(lines[heading_start:])
                    remaining = "\n".join(lines[:heading_start]).rstrip()
                    # move heading to current chunk if remaining is non-empty
                    if remaining:
                        final_chunks[-1]["text"] = remaining
                        text = heading + "\n\n" + text

            final_chunks.append(
                {
                    "text": text,
                    "metadata": {
                        "sequence_number": sequence_number,
                    },
                }
            )
            sequence_number += 1

    return final_chunks


def chunk_documents(
    documents: list[dict], batch_size: int | None, chunk_options: ChunkOptions
) -> tuple[list[list[dict]], int, int, int]:
    total_characters = 0
    total_chunks = 0
    total_batches = 0

    batches = []  # [{text: str, metadata: dict}][]

    for doc in documents:
        page_number = doc["page"] if "page" in doc else None
        text: str = doc["text"]

        if chunk_options.delimiter is None:
            chunks = parse_markdown(text, chunk_options)
        else:
            chunks = []
            # split the text using the delimiter
            splits = text.split(chunk_options.delimiter)
            for split in splits:
                if len(split.strip()) == 0:
                    continue
                chunks.extend(parse_markdown(split, chunk_options))

        for chunk in chunks:
            chunk_dict = {
                "id": str(uuid.uuid4()),
                "text": chunk["text"],
                "metadata": {"sequence_number": total_chunks},
            }

            if page_number is not None:
                chunk_dict["metadata"]["page_number"] = page_number

            total_chunks += 1
            total_characters += len(chunk_dict["text"])

            # disable batching
            if batch_size is None:
                batches.append(chunk_dict)
            else:
                if len(batches) == 0 or len(batches[-1]) % batch_size == 0:
                    # Start new batch
                    batches.append([chunk_dict])
                    total_batches += 1
                else:
                    # Add to current batch
                    batches[-1].append(chunk_dict)

    return batches, total_characters, total_chunks, total_batches
