"""When does the EIA Weekly Petroleum Status Report actually come out?

Normally Wednesday 10:30 am ET. In weeks with a US federal holiday it moves to Thursday
(12:00 pm ET). Source: https://www.eia.gov/petroleum/supply/weekly/schedule.php
Add new years' alternate dates from that page as they are published.
"""
from __future__ import annotations

from datetime import date, timedelta

# Alternate (delayed) release dates, all Thursdays
ALTERNATE_RELEASES = {
    date(2025, 1, 2), date(2025, 1, 23), date(2025, 2, 20), date(2025, 5, 29),
    date(2025, 9, 4), date(2025, 10, 16), date(2025, 11, 13),
    date(2026, 1, 22), date(2026, 2, 19), date(2026, 5, 28), date(2026, 9, 10),
    date(2026, 10, 15), date(2026, 11, 12),
}


def is_release_day(d: date | str) -> bool:
    """True if the weekly petroleum report is released on this date (US Eastern)."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    if d in ALTERNATE_RELEASES:
        return True
    # a normal Wednesday, unless that week's release was pushed to Thursday
    return d.weekday() == 2 and (d + timedelta(days=1)) not in ALTERNATE_RELEASES
