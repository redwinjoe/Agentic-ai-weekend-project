"""End-to-end tests for Hostel Complaints Desk: queue, worker, supervisor, specialists, crash, replay."""
import pytest

from app.providers import demo_providers
from app.worker import Worker
from tests.conftest import SimulatedCrash

PRIYA_REQ = "My tap is leaking in Room 101, please raise a complaint, assign warden, and notify me."


def ask(store, roll_no, text):
    thread = store.create_thread(roll_no)
    return thread, store.enqueue(thread, text, "mock")


def test_a_question_goes_all_the_way_through(store, db):
    thread, run_id = ask(store, "22CS045", PRIYA_REQ)
    assert Worker(store, db, demo_providers(), worker_id="w").run_until_idle() == [(run_id, "succeeded")]
    run = store.get_run(run_id)
    assert [s["tool_name"] for s in run["steps"] if s["kind"] == "tool"] == ["ask_info", "ask_desk"]
    assert "logged for Room 101" in store.load_history(thread)[-1]["text"]
    assert db.count("complaint") == 2 and db.count("notification") == 1


def test_policy_refusal_is_a_normal_answer(store, db):
    thread, run_id = ask(store, "22EC031", "Can I raise another Plumbing complaint for Room 103?")
    Worker(store, db, demo_providers(), worker_id="w").run_until_idle()
    assert store.get_run(run_id)["status"] == "succeeded"
    assert "already has an open complaint" in store.load_history(thread)[-1]["text"]
    assert db.count("complaint") == 1 and db.count("notification") == 0


def test_crash_inside_a_specialist_after_the_complaint_does_not_duplicate_it(store, db, clock):
    _, run_id = ask(store, "22CS045", PRIYA_REQ)
    real_once = db.once

    def once_then_die(key, tool_name, effect):
        result = real_once(key, tool_name, effect)
        if tool_name == "raise_complaint":
            raise SimulatedCrash()
        return result

    db.once = once_then_die
    with pytest.raises(SimulatedCrash):
        Worker(store, db, demo_providers(), worker_id="A", lease_seconds=30).run_once()
    db.once = real_once
    assert db.count("complaint") == 2 and store.get_run(run_id)["status"] == "running"

    clock.advance(31)
    assert Worker(store, db, demo_providers(), worker_id="B", lease_seconds=30).run_until_idle() == [(run_id, "succeeded")]
    assert db.count("complaint") == 2 and db.count("notification") == 1
    assert store.get_run(run_id)["attempts"] == 2


def test_asking_twice_still_one_complaint(store, db):
    for _ in range(2):
        ask(store, "22CS045", PRIYA_REQ)
    Worker(store, db, demo_providers(), worker_id="w").run_until_idle()
    assert db.count("complaint") == 2 and db.count("notification") == 1


def test_run_cancellation(store, db):
    _, run_id = ask(store, "22CS045", PRIYA_REQ)
    res = store.request_cancel(run_id)
    assert res == "cancelled"
    assert store.get_run(run_id)["status"] == "cancelled"
