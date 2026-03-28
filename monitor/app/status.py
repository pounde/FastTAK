"""Health status levels for the monitoring system."""

from enum import IntEnum


class Status(IntEnum):
    ok = 0
    note = 1
    warning = 2
    critical = 3
