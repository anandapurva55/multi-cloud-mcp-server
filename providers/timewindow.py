"""Shared validation for the since_minutes / until_minutes log-window parameters."""

DEFAULT_SINCE_MINUTES = 60
MAX_SINCE_MINUTES = 60 * 24 * 30  # 30 days


def validate_window(since_minutes: int, until_minutes: int) -> None:
    if since_minutes <= 0:
        raise ValueError("since_minutes must be greater than 0.")
    if until_minutes < 0:
        raise ValueError("until_minutes cannot be negative.")
    if until_minutes >= since_minutes:
        raise ValueError("until_minutes must be smaller than since_minutes (the window would be empty).")
    if since_minutes > MAX_SINCE_MINUTES:
        raise ValueError(f"since_minutes cannot exceed {MAX_SINCE_MINUTES} (30 days).")
