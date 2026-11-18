"""Output shaping shared by tools: bounded cells, explicit truncation notes."""
import json

MAX_CELL_CHARS = 2000


def clip_cell(value):
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return f"{value[:MAX_CELL_CHARS]}… [truncated {len(value) - MAX_CELL_CHARS} chars]"
    return value


def rows_json(rows: list, max_rows: int, more_available: bool, hint: str = "") -> str:
    """JSON-encode result rows (cells clipped), appending a note if rows were cut off
    so a model can't mistake a partial result for the whole answer."""
    rows = [{k: clip_cell(v) for k, v in row.items()} for row in rows[:max_rows]]
    text = json.dumps(rows, indent=2, default=str)
    if more_available:
        text += f"\n[Showing the first {len(rows)} rows; more rows are available.{(' ' + hint) if hint else ''}]"
    return text


def list_note(shown: int, truncated: bool) -> str:
    """Trailing note for list-style tools; empty when the list is complete."""
    return f"\n[Showing the first {shown} items; more exist and were not listed.]" if truncated else ""
