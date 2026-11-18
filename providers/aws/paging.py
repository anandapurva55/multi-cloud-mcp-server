"""boto3 pagination with an explicit cap, so list tools neither stop silently at the
first page nor pull back an unbounded number of items."""
from providers.output import list_note

MAX_LIST_ITEMS = 200


def paginate(client, operation: str, key: str, limit: int = MAX_LIST_ITEMS, **kwargs):
    """Return (items, truncated) for a paginated boto3 list operation."""
    paginator = client.get_paginator(operation)
    items = []
    for page in paginator.paginate(PaginationConfig={"MaxItems": limit + 1}, **kwargs):
        items.extend(page.get(key, []))
    truncated = len(items) > limit
    return items[:limit], truncated


__all__ = ["MAX_LIST_ITEMS", "paginate", "list_note"]
