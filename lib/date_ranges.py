"""Helpers for converting UI date ranges into timezone-aware epoch boundaries."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"


def local_date_range_to_epochs(
    date_from: str | None,
    date_to: str | None,
    timezone_name: str | None,
) -> tuple[float | None, float | None]:
    """Return inclusive-date boundaries as [start, end) Unix epochs.

    Empty endpoints are allowed for views with optional date filtering.  The
    configured application timezone is used so a server running in UTC cannot
    shift calls into an adjacent calendar day.
    """
    try:
        timezone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo(DEFAULT_TIMEZONE)

    try:
        start_date = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
        end_date = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError as exc:
        raise ValueError("invalid date format (expected YYYY-MM-DD)") from exc

    if start_date and end_date and end_date < start_date:
        raise ValueError("date_to must be on or after date_from")

    start_epoch = (
        datetime.combine(start_date, time.min, tzinfo=timezone).timestamp()
        if start_date else None
    )
    end_epoch = (
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone).timestamp()
        if end_date else None
    )
    return start_epoch, end_epoch
