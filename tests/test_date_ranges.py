from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lib.date_ranges import local_date_range_to_epochs


def test_eastern_date_range_respects_spring_dst_change():
    start, end = local_date_range_to_epochs("2026-03-08", "2026-03-08", "America/New_York")
    assert end - start == 23 * 3600
    assert datetime.fromtimestamp(start, ZoneInfo("America/New_York")).hour == 0


def test_eastern_date_range_respects_fall_dst_change():
    start, end = local_date_range_to_epochs("2026-11-01", "2026-11-01", "America/New_York")
    assert end - start == 25 * 3600


@pytest.mark.parametrize("start,end", [("2026-02-30", "2026-03-01"), ("2026-03-02", "2026-03-01")])
def test_invalid_date_ranges_are_rejected(start, end):
    with pytest.raises(ValueError):
        local_date_range_to_epochs(start, end, "America/New_York")
