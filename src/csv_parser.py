from .file_type import ExtractedFile
import re


def _compact_markdown(md: str) -> str:
    # remove spaces right after | and right before |
    lines = md.split("\n")
    new_lines = []
    for line in lines:
        line = re.sub(r"\|\s+", "|", line)
        line = re.sub(r"\s+\|", "|", line)
        new_lines.append(line)

    return "\n".join(new_lines)


def parse_csv(file: ExtractedFile) -> str:
    import pandas as pd
    import csv

    dialect = csv.Sniffer().sniff(file.file.getvalue().decode("utf-8"))
    delimiter = dialect.delimiter

    df = pd.read_csv(file.file, sep=delimiter)
    df = df.dropna(axis=1, how="all")  # drop columns that are all empty
    df.fillna("", inplace=True)  # fill empty cells with empty string

    markdown = df.to_markdown(index=False, tablefmt="github")
    return _compact_markdown(markdown)
