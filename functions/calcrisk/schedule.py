"""Which scheduled runs to skip."""

from datetime import datetime

from calcrisk.config import QUIET_HOURS, QUIET_HOURS_HOURLY


def skip_for_quiet_hours(local_now: datetime) -> bool:
    """True for the half-past run during the quiet hours, if hourly-overnight
    is switched on. local_now must be in Denver time."""
    if not QUIET_HOURS_HOURLY:
        return False
    start, end = QUIET_HOURS
    return start <= local_now.hour < end and local_now.minute >= 30
