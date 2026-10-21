from services.reminder import filter_executives


def _user(title, manager=True, manager_active=True):
    user = {"Id": "005", "Name": "X", "Email": "x@x.com", "Title": title, "ManagerId": None, "Manager": None}
    if manager:
        user["ManagerId"] = "005M"
        user["Manager"] = {"Name": "Lider", "Email": "l@x.com", "IsActive": manager_active}
    return user


def test_blocked_titles_are_removed_case_insensitive():
    users = [_user("Ops Analyst"), _user("Sales Development Representative")]
    kept = filter_executives(users, {"ops analyst"}, require_leader=True)
    assert [u["Title"] for u in kept] == ["Sales Development Representative"]


def test_users_without_active_leader_are_removed():
    users = [_user("SDR", manager=False), _user("SDR", manager_active=False), _user("SDR")]
    assert len(filter_executives(users, set(), require_leader=True)) == 1
    assert len(filter_executives(users, set(), require_leader=False)) == 3


def test_people_with_reports_count_only_as_leaders():
    boss = _user("Business Development Manager")
    boss["Id"] = "005BOSS"
    rep = _user("Business Development Executive")
    rep["ManagerId"] = "005BOSS"
    kept = filter_executives([boss, rep], set(), require_leader=True, exclude_leaders=True)
    assert [u["Title"] for u in kept] == ["Business Development Executive"]
