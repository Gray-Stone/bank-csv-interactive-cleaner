"""Interactive parsing setup for messy tabular exports."""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from .io_loader import LoadedSheet
from .profile_store import build_header_signature


@dataclass
class ParsedTable:
    """Represent parsed headers, rows, and parse hints."""
    headers: list[str]
    rows: list[dict[str, str]]
    parse_hints: dict[str, object]


def _safe_int(value: str, fallback: int, minimum: int, maximum: int) -> int:
    """Internal helper for safe int."""
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    return max(minimum, min(parsed, maximum))


def _prompt(message: str, default: str | None = None) -> str:
    """Internal helper for prompt."""
    if default is None:
        prompt_label = message
    else:
        prompt_label = f"{message} [default: {default}] (press Enter to keep)"
    raw = input(f"{prompt_label}: ").strip()
    if raw:
        return raw
    return default or ""


def _is_interactive() -> bool:
    """Internal helper for is interactive."""
    return sys.stdin.isatty()


def _dedupe_headers(headers: list[str]) -> list[str]:
    """Internal helper for dedupe headers."""
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for index, header in enumerate(headers):
        base = header.strip() or f"column_{index + 1}"
        if base not in seen:
            seen[base] = 1
            deduped.append(base)
            continue
        seen[base] += 1
        deduped.append(f"{base}_{seen[base]}")
    return deduped


def _auto_header_row(raw_rows: list[list[str]], max_scan: int = 30) -> int:
    """Internal helper for auto header row."""
    best_index = 0
    best_score = -1.0
    for index, row in enumerate(raw_rows[:max_scan]):
        non_empty = [cell for cell in row if cell.strip()]
        if not non_empty:
            continue
        text_cells = sum(1 for cell in non_empty if any(char.isalpha() for char in cell))
        score = len(non_empty) * 3 + text_cells
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _print_preview(raw_rows: list[list[str]], max_rows: int = 12) -> None:
    """Internal helper for print preview."""
    print("\nPreview (row index => values):")
    for index, row in enumerate(raw_rows[:max_rows]):
        display = " | ".join(row[:8])
        print(f"{index:>3}: {display}")
    if len(raw_rows) > max_rows:
        print(f"... ({len(raw_rows) - max_rows} more rows)")
    print("")


def _reparse_csv(path: Path, encoding: str, delimiter: str) -> list[list[str]]:
    """Internal helper for reparse csv."""
    text = path.read_bytes().decode(encoding, errors="replace")
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    return [[str(cell).strip() for cell in row] for row in reader]


def parse_with_wizard(loaded: LoadedSheet) -> ParsedTable:
    """Parse with wizard."""
    interactive = _is_interactive()
    raw_rows = loaded.rows
    delimiter = loaded.delimiter_guess
    encoding = loaded.encoding_guess

    if interactive:
        print("\nStep 1/3 - Choose file parsing setup")
        print("This step decides how to read rows before mapping fields.")
        print(f"\nLoaded {loaded.file_path.name} ({loaded.file_type}, sheet={loaded.sheet_name})")
        _print_preview(raw_rows)

    if loaded.file_type == "csv" and interactive:
        delimiter_input = _prompt(
            "Delimiter character (comma ',', semicolon ';', tab '\\t')",
            default=delimiter,
        )
        delimiter = delimiter_input or delimiter
        if delimiter != loaded.delimiter_guess:
            raw_rows = _reparse_csv(loaded.file_path, encoding=encoding, delimiter=delimiter)
            print("\nReparsed file with selected delimiter.")
            _print_preview(raw_rows)

    guessed_header = _auto_header_row(raw_rows)
    guessed_data_start = min(guessed_header + 1, max(0, len(raw_rows) - 1))

    if interactive:
        header_row = _safe_int(
            _prompt(
                "Header row index (row with column names like Date/Description/Amount)",
                default=str(guessed_header),
            ),
            fallback=guessed_header,
            minimum=0,
            maximum=max(0, len(raw_rows) - 1),
        )
        first_data_row = _safe_int(
            _prompt(
                "First data row index (first real transaction row)",
                default=str(guessed_data_start),
            ),
            fallback=guessed_data_start,
            minimum=0,
            maximum=max(0, len(raw_rows)),
        )
    else:
        header_row = guessed_header
        first_data_row = guessed_data_start

    if first_data_row <= header_row:
        first_data_row = header_row + 1
        if interactive:
            print(
                f"Adjusted first data row to {first_data_row} so it stays after header row {header_row}."
            )

    headers = raw_rows[header_row] if raw_rows else []
    headers = _dedupe_headers(headers)
    width = len(headers)

    normalized_rows: list[dict[str, str]] = []
    for source_row_index, row in enumerate(raw_rows[first_data_row:], start=first_data_row):
        if not any(cell.strip() for cell in row):
            continue
        padded = list(row[:width]) + [""] * max(0, width - len(row))
        normalized_rows.append(
            {
                "__source_row_index": str(source_row_index),
                **{headers[col_idx]: padded[col_idx] for col_idx in range(width)},
            }
        )

    parse_hints: dict[str, object] = {
        "file_type": loaded.file_type,
        "sheet_name": loaded.sheet_name,
        "encoding": encoding,
        "delimiter": delimiter,
        "header_row_index": header_row,
        "first_data_row_index": first_data_row,
        "header_signature": build_header_signature(headers=headers, delimiter=delimiter),
    }
    if interactive:
        print("\nSelected parse setup:")
        print(f"- Delimiter: {delimiter!r}")
        print(f"- Header row: {header_row}")
        print(f"- First data row: {first_data_row}")
        print(f"- Parsed transactions: {len(normalized_rows)}")
    return ParsedTable(headers=headers, rows=normalized_rows, parse_hints=parse_hints)
