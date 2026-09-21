"""Supervisor and specialist agent tests for Hostel Complaints Desk."""
from app.agents import SupervisorTools, run_specialist, run_tool
from app.providers import ModelTurn, ScriptedProvider, ToolCall, demo_providers
from app.tools.hostel_tools import InfoTools


def test_the_supervisor_only_has_delegation_tools(db):
    tools = SupervisorTools(db, demo_providers(), "22CS045")
    assert set(tools.functions()) == {"ask_info", "ask_desk"}
    assert set(tools.DELEGATES) == set(tools.TOOL_NAMES)


def test_info_specialist_answers_a_question(db):
    result, replayed = run_tool(SupervisorTools(db, demo_providers(), "22CS045"), db, "k1", "ask_info",
                                {"question": "Check open complaints for Room 101"})
    assert result["agent"] == "info" and result["tools_used"] == ["find_complaint"] and not replayed
    assert "No active complaint" in result["answer"]


def test_desk_specialist_raises_and_notifies_for_the_bound_student(db):
    result, _ = run_tool(SupervisorTools(db, demo_providers(), "22CS045"), db, "k1", "ask_desk",
                         {"request": "Raise a Plumbing complaint for Room 101, assign Mr. Ramesh, and text student to confirm."})
    assert result["tools_used"] == ["check_can_raise_complaint", "raise_complaint", "assign_warden", "notify_student"]
    assert db.count("complaint") == 2      # Seed (1) + new (1)
    assert db.count("notification") == 1


def test_a_repeated_delegation_with_the_same_key_does_nothing_twice(db):
    tools = SupervisorTools(db, demo_providers(), "22CS045")
    args = {"request": "Raise a Plumbing complaint for Room 101, assign Mr. Ramesh, and text student to confirm."}
    run_tool(tools, db, "same-key", "ask_desk", args)
    run_tool(tools, db, "same-key", "ask_desk", args)
    assert db.count("complaint") == 2 and db.count("notification") == 1 and db.count("idempotency") == 3


def test_bad_delegation_arguments_are_fed_back(db):
    result, _ = run_tool(SupervisorTools(db, demo_providers(), "22CS045"), db, "k", "ask_desk", {"request": ""})
    assert result["error"] == "invalid_arguments"


def test_a_looping_specialist_stops(db):
    looping = ScriptedProvider([ModelTurn(text=None, tool_calls=[ToolCall("find_complaint", {"text": "101"})])], loop=True)
    result = run_specialist("info", "sys", InfoTools(db), db=db, provider=looping, task="x", parent_key="k")
    assert result["error"] == "specialist_step_limit"


def test_specialists_see_only_their_own_task(db):
    providers = demo_providers()
    run_tool(SupervisorTools(db, providers, "22CS045"), db, "k", "ask_info", {"question": "Check open complaints for Room 101"})
    seen = providers["info"].calls[0]
    assert seen == [{"role": "user", "text": "Check open complaints for Room 101"}]
    assert providers["desk"].calls == []
