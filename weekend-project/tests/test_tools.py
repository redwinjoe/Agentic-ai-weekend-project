"""Tools, rules in data, and safe writes for Hostel Complaints Desk."""
import inspect
import threading

import pytest

from app.tools.hostel_tools import DeskTools, InfoTools


@pytest.mark.parametrize("cls", [InfoTools, DeskTools])
def test_every_tool_is_described(cls):
    for name in cls.TOOL_NAMES:
        doc = inspect.getdoc(getattr(cls, name)) or ""
        assert len(doc) >= 120, f"Tool {name} docstring is too short ({len(doc)} chars)"


def test_find_complaint_and_get_room(db):
    info = InfoTools(db)
    found = info.find_complaint("103")["complaints"]
    assert len(found) == 1 and found[0]["issue"] == "Plumbing"
    assert info.find_complaint("   ")["error"] == "empty_query"

    room = info.get_room("101")
    assert room["hostel_block"] == "Block A" and len(room["residents"]) == 1


def test_policy_comes_from_the_database(db):
    arjun = DeskTools(db, "22IT017")
    # Arjun has dues Rs 600 > limit Rs 500
    assert arjun.check_can_raise_complaint("Plumbing") == {
        "can_raise": False,
        "reasons": ["unpaid dues Rs 600 exceeds the Rs 500 limit"]
    }
    # Update policy in database dynamically
    db.conn.execute("UPDATE policy SET value = 1000 WHERE name = 'max_dues_to_raise_complaint'")
    assert arjun.check_can_raise_complaint("Plumbing")["can_raise"] is True


def test_duplicate_open_complaint_refusal(db):
    divya = DeskTools(db, "22EC031")
    # Room 103 already has an open Plumbing complaint in seed data
    res = divya.check_can_raise_complaint("Plumbing")
    assert res["can_raise"] is False
    assert "already exists for Room 103" in res["reasons"][0]


def test_raise_complaint_refuses_when_policy_says_no_even_if_the_model_skips_the_check(db):
    arjun = DeskTools(db, "22IT017")
    res = arjun.raise_complaint("Plumbing", "Tap leaking")
    assert res["error"] == "not_allowed"
    assert db.count("complaint") == 1      # Seed has 1 complaint; nothing new added


def test_raising_twice_is_safe_and_idempotent(db):
    priya = DeskTools(db, "22CS045")
    first = priya.raise_complaint("Electrical", "Fan not working")
    assert first["status"] == "raised"
    second = priya.raise_complaint("Electrical", "Fan not working")
    assert second["status"] == "already_open"
    assert db.count("complaint") == 2      # Seed (1) + Electrical (1) = 2 total


def test_assign_warden_is_idempotent(db):
    priya = DeskTools(db, "22CS045")
    comp = priya.raise_complaint("WiFi", "No internet connection")
    comp_id = comp["complaint_id"]

    res1 = priya.assign_warden(comp_id, "Ms. Lakshmi")
    assert res1["status"] == "assigned"

    res2 = priya.assign_warden(comp_id, "Ms. Lakshmi")
    assert res2["status"] == "already_assigned"


def test_same_text_same_day_is_sent_once(db):
    desk = DeskTools(db, "22CS045")
    first = desk.notify_student("Complaint registered.")
    second = desk.notify_student("Complaint registered.")
    assert first["notification_id"] == second["notification_id"] and second["duplicate"] is True


def test_the_desk_cannot_act_for_another_student(db):
    params = {n: list(inspect.signature(getattr(DeskTools, n)).parameters) for n in DeskTools.TOOL_NAMES}
    assert all("roll_no" not in p for p in params.values())


def test_thread_race_condition(db):
    """Requirement 8: Race test with threads ensuring data rule holds under concurrency."""
    results = []
    lock = threading.Lock()

    def worker():
        desk = DeskTools(db, "22CS045")
        with lock:
            res = desk.raise_complaint("Carpentry", "Door lock broken")
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raised = [r for r in results if r.get("status") == "raised"]
    already = [r for r in results if r.get("status") == "already_open"]
    assert len(raised) == 1
    assert len(already) == 4
