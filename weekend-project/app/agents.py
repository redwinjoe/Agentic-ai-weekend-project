"""Hostel Complaints Desk Agents: Supervisor and two specialists.

    student ──▶ supervisor ──ask_info──▶ info specialist  (find_complaint, get_room)
                          └─ask_desk──▶ desk specialist  (get_student, check_can_raise_complaint,
                                                         raise_complaint, assign_warden, notify_student)

Each specialist is an ordinary agent loop with its own system prompt and its own small tool set.
To the supervisor, a specialist is just a tool: "agent as tool".
"""
import time
from collections.abc import Callable

from app.hostel_db import HostelDb
from app.idempotency import idempotency_key
from app.providers import AgentError
from app.tools.hostel_tools import DeskTools, InfoTools, Toolset

SPECIALIST_MAX_STEPS = 6

SUPERVISOR_SYSTEM = """You are the Hostel Complaints Desk Assistant, talking to the student with roll number {roll_no}.
You never search complaints or modify records yourself. Delegate:
- ask_info for finding complaints and checking room details;
- ask_desk for anything about logging complaints, warden assignments, or student notifications.
Give each specialist a complete, specific request, including room numbers and issue details once known.
Then answer the student briefly, using only what the specialists reported."""

INFO_SYSTEM = """You are the info specialist of the hostel complaints desk. Find complaints and report
their complaint_id, room_number, issue, status, and assigned warden. You cannot raise or modify anything. Be brief."""

DESK_SYSTEM = """You are the complaints desk specialist, acting for student {roll_no} only.
Always call check_can_raise_complaint before raise_complaint. Never decide policy yourself: report the reasons
the tools give. Confirm a successfully raised complaint with notify_student. Report what you did, briefly."""


def run_tool(toolset: Toolset, db: HostelDb, key: str, name: str, args: dict) -> tuple[dict, bool]:
    """Run one tool call for any agent. Returns (result, replayed). Never raises, except AgentError.

    Side effects run at most once per key; replayed is True when stored result was returned.
    """
    try:
        if name in toolset.DELEGATES:
            return toolset.delegate(name, args, key), False
        if name in toolset.SIDE_EFFECTS:
            result, fresh = db.once(key, name, lambda: toolset.call(name, args))
            return result, not fresh
        return toolset.call(name, args), False
    except AgentError:
        raise
    except NotImplementedError:
        return {"error": "not_implemented", "hint": f"{name} is not available yet."}, False
    except Exception as e:
        return {"error": "tool_failed", "hint": f"{name} failed ({type(e).__name__}). Try another way or tell the user."}, False


def run_specialist(agent: str, system: str, toolset: Toolset, *, db: HostelDb, provider, task: str,
                   parent_key: str, on_step: Callable[[dict], None] | None = None) -> dict:
    """A specialist's whole agent loop, run inside one tool call of the supervisor."""
    contents = [{"role": "user", "text": task}]
    functions = list(toolset.functions().values())
    used = []
    seq = 0
    while seq < SPECIALIST_MAX_STEPS:
        turn = provider.generate(system, contents, functions)
        seq += 1
        if not turn.tool_calls:
            return {"agent": agent, "answer": turn.text or "", "tools_used": used}
        contents.append({"role": "model", "text": turn.text, "raw": turn.raw,
                         "tool_calls": [{"name": c.name, "args": c.args} for c in turn.tool_calls]})
        for call in turn.tool_calls:
            seq += 1
            key = idempotency_key(parent_key, seq, call.name, call.args)
            started = time.perf_counter()
            result, replayed = run_tool(toolset, db, key, call.name, call.args)
            used.append(call.name)
            if on_step:
                on_step({"agent": agent, "kind": "tool", "tool": call.name, "args": call.args, "result": result,
                         "ok": "error" not in result, "replayed": replayed,
                         "ms": round((time.perf_counter() - started) * 1000)})
            contents.append({"role": "tool", "name": call.name, "result": result})
    return {"agent": agent, "error": "specialist_step_limit", "tools_used": used,
            "hint": "The specialist could not finish. Tell the student to try a simpler request."}


class SupervisorTools(Toolset):
    """The supervisor's only tools are the two specialists."""

    TOOL_NAMES = ("ask_info", "ask_desk")
    DELEGATES = ("ask_info", "ask_desk")

    def __init__(self, db: HostelDb, providers: dict, roll_no: str, on_step=None):
        self.db, self.providers, self.roll_no, self.on_step = db, providers, roll_no, on_step

    def ask_info(self, question: str) -> dict:
        """Ask the info specialist to find complaints or check room status.

        Use for 'is there an open complaint for Room 101', 'check room 103 status', 'find plumbing complaints'. It cannot raise complaints.

        Args:
            question: A complete request, e.g. "Check if there is an open complaint for Room 101."

        Returns:
            {"agent": "info", "answer": str, "tools_used": [str]}.
        """
        raise RuntimeError("delegations run through delegate()")

    def ask_desk(self, request: str) -> dict:
        """Ask the complaints desk specialist to act on this student's room/account. IT CAN CHANGE DATA:
        raise complaints, assign wardens, and send student notifications.

        Use for raising complaints, warden assignment, checking complaint eligibility, and confirmations.
        Include room and issue details from info specialist when raising.

        Args:
            request: A complete instruction, e.g. "Raise a Plumbing complaint for Room 101, assign Mr. Ramesh, and text student."

        Returns:
            {"agent": "desk", "answer": str, "tools_used": [str]}.
        """
        raise RuntimeError("delegations run through delegate()")

    def delegate(self, name: str, args: dict, key: str) -> dict:
        bad = self.call_check(name, args)
        if bad:
            return bad
        if self.on_step:
            self.on_step({"agent": "supervisor", "kind": "delegate", "tool": name, "args": args})
        if name == "ask_info":
            return run_specialist("info", INFO_SYSTEM, InfoTools(self.db), db=self.db,
                                  provider=self.providers["info"], task=args["question"],
                                  parent_key=key, on_step=self.on_step)
        return run_specialist("desk", DESK_SYSTEM.format(roll_no=self.roll_no), DeskTools(self.db, self.roll_no),
                              db=self.db, provider=self.providers["desk"], task=args["request"],
                              parent_key=key, on_step=self.on_step)

    def call_check(self, name: str, args: dict) -> dict | None:
        """Validate a delegation's arguments."""
        field = "question" if name == "ask_info" else "request"
        if set(args) != {field} or not isinstance(args[field], str) or not args[field].strip():
            return {"error": "invalid_arguments", "hint": f"{name} takes one non-empty string: {field}."}
        return None
