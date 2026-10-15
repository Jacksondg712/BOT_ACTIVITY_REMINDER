from datetime import datetime
from zoneinfo import ZoneInfo

from services.scheduler import due_slot

TZ = ZoneInfo("America/Bogota")
SCHEDULE = {"times": ["08:00", "14:00"], "catchup_minutes": 60}


def _at(hour, minute):
    return datetime(2026, 10, 14, hour, minute, tzinfo=TZ)


def test_nothing_due_before_first_slot():
    assert due_slot(_at(7, 59), SCHEDULE, []) is None


def test_slot_due_within_catchup():
    assert due_slot(_at(8, 0), SCHEDULE, []) == "08:00"
    assert due_slot(_at(8, 45), SCHEDULE, []) == "08:00"


def test_slot_skipped_after_catchup():
    assert due_slot(_at(9, 30), SCHEDULE, []) is None


def test_slot_not_repeated_once_sent():
    assert due_slot(_at(8, 10), SCHEDULE, ["08:00"]) is None
    assert due_slot(_at(14, 5), SCHEDULE, ["08:00"]) == "14:00"
