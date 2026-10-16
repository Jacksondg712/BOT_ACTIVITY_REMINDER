from datetime import datetime
from zoneinfo import ZoneInfo

import services.reminder as reminder
from services.models import leader_from_user
from services.salesforce_client import SalesforceClient, SalesforceSession

TZ = ZoneInfo("America/Bogota")


def _user(manager_active):
    return {
        "Id": "005A",
        "ManagerId": "005L",
        "Manager": {"Name": "Edna Guerrero", "FirstName": "Edna", "Email": "Edna@X.com", "IsActive": manager_active},
    }


def test_active_manager_is_leader():
    leader = leader_from_user(_user(True))
    assert leader.id == "005L"
    assert leader.email == "edna@x.com"


def test_inactive_manager_is_not_leader():
    assert leader_from_user(_user(False)) is None


def test_links_use_session_instance_url():
    sf = SalesforceClient()
    sf._session = SalesforceSession(access_token="t", instance_url="https://real.my.salesforce.com")
    assert sf.record_url("Task", "00T1") == "https://real.my.salesforce.com/lightning/r/Task/00T1/view"
    assert sf.tasks_home_url() == "https://real.my.salesforce.com/lightning/o/Task/home"


def test_skip_reason_on_weekend_and_holiday(monkeypatch):
    monkeypatch.setitem(reminder.reminder_config, "schedule", {"business_days_only": True})
    assert reminder.skip_reason(datetime(2026, 10, 17, 8, 0, tzinfo=TZ)) == "sábado"
    assert reminder.skip_reason(datetime(2026, 10, 12, 8, 0, tzinfo=TZ)) is not None  # festivo (Dia de la Raza)
    assert reminder.skip_reason(datetime(2026, 10, 14, 8, 0, tzinfo=TZ)) is None


def test_skip_reason_disabled(monkeypatch):
    monkeypatch.setitem(reminder.reminder_config, "schedule", {"business_days_only": False})
    assert reminder.skip_reason(datetime(2026, 10, 17, 8, 0, tzinfo=TZ)) is None


def test_test_leader_groups_all_test_executives(monkeypatch):
    import asyncio

    from services.models import ExecutiveReport, Person

    monkeypatch.setattr(reminder.settings, "ejecutivotestsend1", "Lider.Prueba@x.com")

    class FakeSF:
        async def find_active_users_by_emails(self, emails):
            assert emails == ["lider.prueba@x.com"]
            return []

    reports = [
        ExecutiveReport(executive=Person(f"005{i}", f"Ejecutivo {i}", "E", f"e{i}@x.com"), leader=None)
        for i in range(3)
    ]
    groups = asyncio.run(reminder.test_leader_groups(FakeSF(), reports))

    assert len(groups) == 1
    leader, members = groups[0]
    assert leader.email == "lider.prueba@x.com"
    assert leader.first_name == "Lider"
    assert members == reports
