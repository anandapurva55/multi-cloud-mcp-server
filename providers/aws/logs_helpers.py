"""Shared CloudWatch Logs fetch/filter logic used by ecs_tools, lambda_tools and codebuild_tools.

CloudWatch's filter_log_events has no descending order: it returns events oldest-first
from startTime, `limit` only caps a single page, and a page can come back EMPTY while a
nextToken still points at more matches (common with a filterPattern on a sparse group).
So "the most recent N" means: bound the search with a time window, follow nextToken until
the window is exhausted, and keep the last N events seen.
"""
import json
import time
from collections import deque
from datetime import datetime, timezone

from providers.timewindow import DEFAULT_SINCE_MINUTES, MAX_SINCE_MINUTES, validate_window  # noqa: F401

ERROR_PATTERN = '?ERROR ?Error ?CRITICAL ?FATAL ?Exception ?Traceback'

MAX_PAGES = 100                           # safety cap on API calls per tool call


def fetch_log_events(logs_client, log_group: str, limit: int, errors_only: bool,
                     since_minutes: int = DEFAULT_SINCE_MINUTES, until_minutes: int = 0) -> str:
    """Return the most recent `limit` log events in [now-since_minutes, now-until_minutes]."""
    validate_window(since_minutes, until_minutes)
    limit = max(1, min(limit, 200))
    now_ms = int(time.time() * 1000)
    kwargs = dict(logGroupName=log_group, startTime=now_ms - since_minutes * 60_000)
    if until_minutes:
        # Omitted when 0 (= "now"): event timestamps are producer-supplied and may run
        # slightly ahead of this machine's clock, and an endTime of "now" would hide them.
        kwargs["endTime"] = now_ms - until_minutes * 60_000
    if errors_only:
        kwargs["filterPattern"] = ERROR_PATTERN

    tail = deque(maxlen=limit)
    pages = 0
    scan_capped = False
    token = None
    try:
        while True:
            if token:
                kwargs["nextToken"] = token
            resp = logs_client.filter_log_events(**kwargs)
            tail.extend(resp.get("events", []))
            pages += 1
            token = resp.get("nextToken")
            if not token:
                break
            if pages >= MAX_PAGES:
                scan_capped = True
                break
    except logs_client.exceptions.ResourceNotFoundException:
        return f"Log group '{log_group}' does not exist (yet)."

    window = f"the last {since_minutes} minutes" if until_minutes == 0 else \
        f"{since_minutes} to {until_minutes} minutes ago"
    if not tail:
        return f"No {'error ' if errors_only else ''}logs found in '{log_group}' in {window}."

    rows = [
        {
            "timestamp": e["timestamp"],
            "time": datetime.fromtimestamp(e["timestamp"] / 1000, tz=timezone.utc).isoformat(),
            "logStream": e.get("logStreamName"),
            "message": e.get("message", "").rstrip("\n"),
        }
        for e in tail
    ]
    text = json.dumps(rows, indent=2, default=str)
    if scan_capped:
        text += (f"\n[WARNING: stopped after {MAX_PAGES} pages; these are the newest events within the part of "
                 f"the window that was scanned, NOT necessarily the newest overall. Narrow the window with "
                 f"since_minutes/until_minutes.]")
    return text
