"""Tests for the CloudWatch Logs fetcher against a fake client that behaves like the real
API: events come back OLDEST-FIRST, `limit` caps a single page, and a page may be empty
while a nextToken still points at more matches. (moto implements none of that faithfully,
which is how the original "most recent logs returns the oldest" bug went unnoticed.)"""
import json
import time
from unittest.mock import MagicMock

import pytest

from providers.aws import logs_helpers
from providers.aws.logs_helpers import ERROR_PATTERN, fetch_log_events


class NotFound(Exception):
    pass


def make_client(pages):
    """A client whose filter_log_events returns `pages` in order, following nextToken."""
    client = MagicMock()
    client.exceptions.ResourceNotFoundException = NotFound
    calls = []

    def filter_log_events(**kwargs):
        calls.append(dict(kwargs))
        idx = int(kwargs.get("nextToken", "0"))
        page = dict(pages[idx])
        if idx + 1 < len(pages):
            page["nextToken"] = str(idx + 1)
        return page

    client.filter_log_events.side_effect = filter_log_events
    client.calls = calls
    return client


def ev(i):
    return {"timestamp": 1_700_000_000_000 + i, "logStreamName": "s", "message": f"m{i}\n"}


def messages(result):
    return [r["message"] for r in json.loads(result)]


def test_returns_newest_events_not_the_oldest_when_window_has_more_than_limit():
    client = make_client([{"events": [ev(i) for i in range(10)]}])
    assert messages(fetch_log_events(client, "g", limit=3, errors_only=False)) == ["m7", "m8", "m9"]


def test_tail_is_taken_across_multiple_pages():
    client = make_client([
        {"events": [ev(0), ev(1), ev(2)]},
        {"events": [ev(3), ev(4), ev(5)]},
        {"events": [ev(6), ev(7)]},
    ])
    assert messages(fetch_log_events(client, "g", limit=4, errors_only=False)) == ["m4", "m5", "m6", "m7"]
    assert len(client.calls) == 3


def test_empty_first_page_with_next_token_does_not_report_no_errors():
    # CloudWatch can return an empty page (with a token) before the first match when a filter
    # pattern is used. The old code stopped there and said "No error logs found".
    client = make_client([{"events": []}, {"events": []}, {"events": [ev(5)]}])
    result = fetch_log_events(client, "g", limit=10, errors_only=True)
    assert messages(result) == ["m5"]
    assert all(c["filterPattern"] == ERROR_PATTERN for c in client.calls)


def test_no_matches_after_exhausting_all_pages_says_so_with_window():
    client = make_client([{"events": []}, {"events": []}])
    result = fetch_log_events(client, "g", limit=10, errors_only=True)
    assert result == "No error logs found in 'g' in the last 60 minutes."
    assert len(client.calls) == 2


def test_sends_a_start_time_window_and_omits_end_time_by_default():
    client = make_client([{"events": []}])
    before = int(time.time() * 1000)
    fetch_log_events(client, "g", limit=5, errors_only=False, since_minutes=15)
    kwargs = client.calls[0]
    assert before - 15 * 60_000 - 5_000 <= kwargs["startTime"] <= before - 15 * 60_000 + 5_000
    # no endTime: producer timestamps may be ahead of our clock and must not be hidden
    assert "endTime" not in kwargs
    assert "limit" not in kwargs


def test_until_minutes_sets_end_time_and_describes_window():
    client = make_client([{"events": []}])
    result = fetch_log_events(client, "g", limit=5, errors_only=False, since_minutes=120, until_minutes=60)
    assert "endTime" in client.calls[0]
    assert client.calls[0]["endTime"] - client.calls[0]["startTime"] == 60 * 60_000
    assert "120 to 60 minutes ago" in result


@pytest.mark.parametrize("since,until,msg", [
    (0, 0, "greater than 0"),
    (-5, 0, "greater than 0"),
    (10, -1, "negative"),
    (10, 10, "smaller than since_minutes"),
    (10, 20, "smaller than since_minutes"),
    (60 * 24 * 31, 0, "cannot exceed"),
])
def test_invalid_windows_are_rejected(since, until, msg):
    client = make_client([{"events": []}])
    with pytest.raises(ValueError, match=msg):
        fetch_log_events(client, "g", limit=5, errors_only=False, since_minutes=since, until_minutes=until)
    client.filter_log_events.assert_not_called()


def test_missing_log_group_message():
    client = MagicMock()
    client.exceptions.ResourceNotFoundException = NotFound
    client.filter_log_events.side_effect = NotFound()
    assert fetch_log_events(client, "/aws/lambda/x", 5, False) == "Log group '/aws/lambda/x' does not exist (yet)."


def test_page_cap_is_disclosed_instead_of_silently_returning_partial_data(monkeypatch):
    monkeypatch.setattr(logs_helpers, "MAX_PAGES", 3)
    client = make_client([{"events": [ev(i)]} for i in range(10)])
    result = fetch_log_events(client, "g", limit=50, errors_only=False)
    assert len(client.calls) == 3
    assert "WARNING: stopped after 3 pages" in result


def test_limit_is_clamped_and_floor_is_one():
    client = make_client([{"events": [ev(i) for i in range(500)]}])
    assert len(messages(fetch_log_events(client, "g", limit=10_000, errors_only=False))) == 200
    client = make_client([{"events": [ev(i) for i in range(5)]}])
    assert messages(fetch_log_events(client, "g", limit=0, errors_only=False)) == ["m4"]


def test_rows_include_iso_time():
    client = make_client([{"events": [ev(0)]}])
    row = json.loads(fetch_log_events(client, "g", 5, False))[0]
    assert row["time"].startswith("2023-11-14T22:13:20")
