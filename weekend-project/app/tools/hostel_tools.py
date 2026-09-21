"""Hostel complaint tools, split between two specialist agents. Descriptions are prompts."""
from datetime import datetime, timezone

from app.hostel_db import HostelDb
from app.idempotency import notification_dedupe_key
from app.tools.dispatch import dispatch


class Toolset:
    SIDE_EFFECTS: tuple[str, ...] = ()     # run through HostelDb.once with an idempotency key
    DELEGATES: tuple[str, ...] = ()        # hand work to another agent
    TOOL_NAMES: tuple[str, ...] = ()

    def functions(self) -> dict:
        return {n: getattr(self, n) for n in self.TOOL_NAMES}

    def call(self, name: str, args: dict) -> dict:
        return dispatch(self.functions(), name, args)


class InfoTools(Toolset):
    """Read-only. The info specialist can search complaints and check room status, but never change data."""

    TOOL_NAMES = ("find_complaint", "get_room")

    def __init__(self, db: HostelDb):
        self.db = db

    def find_complaint(self, text: str) -> dict:
        """Find hostel complaints by keyword matching room number, issue type, status, or description.

        Use for queries like 'find complaints for room 101', 'is there an open plumbing complaint',
        or 'status of complaint'. Read-only: changes nothing. To raise or resolve complaints, delegate to desk tools instead.

        Args:
            text: Query string matching room number, issue, or description (e.g. "101", "Plumbing", "open").

        Returns:
            {"complaints": [{"complaint_id", "room_number", "issue", "description", "status", "warden_assigned", "resident_name"}]}.
            An empty list means no matching complaint was found.
        """
        if not text.strip():
            return {"error": "empty_query", "hint": "Pass a non-empty search string for room, issue, or description."}
        complaints = self.db.find_complaints(text)
        return {
            "complaints": [
                {
                    "complaint_id": c["id"],
                    "room_number": c["room_number"],
                    "issue": c["issue"],
                    "description": c["description"],
                    "status": c["status"],
                    "warden_assigned": c["warden_assigned"],
                    "resident_name": c["resident_name"],
                }
                for c in complaints
            ]
        }

    def get_room(self, room_number: str) -> dict:
        """Get details of a hostel room, including its block, resident details, and active complaints.

        Use when checking room status, resident details, or active issues for a specific room number.
        Read-only: changes nothing in the database. Do not use to raise new complaints.

        Args:
            room_number: The room identifier string (e.g. "101", "102").

        Returns:
            {"room_number", "hostel_block", "floor", "residents": [...], "active_complaints": [...]}.
        """
        if not room_number.strip():
            return {"error": "empty_room_number", "hint": "Pass a valid room number."}
        r = self.db.get_room(room_number.strip())
        if r is None:
            return {"error": "unknown_room", "hint": "Check the room number and try again."}
        return r


class DeskTools(Toolset):
    """The complaints desk, bound to ONE student. The model cannot pick a different roll number."""

    TOOL_NAMES = ("get_student", "check_can_raise_complaint", "raise_complaint", "assign_warden", "notify_student")
    SIDE_EFFECTS = ("raise_complaint", "assign_warden", "notify_student")

    def __init__(self, db: HostelDb, roll_no: str, clock=lambda: datetime.now(timezone.utc)):
        self.db, self.roll_no, self.clock = db, roll_no, clock

    def _student(self) -> dict:
        s = self.db.get_student(self.roll_no)
        if s is None:
            raise LookupError(f"student {self.roll_no} not found")
        return s

    def get_student(self) -> dict:
        """Get current student's hostel record: name, room number, pending dues, and active complaints.

        Use for 'what is my room', 'do I have pending dues', or 'my active complaints'.
        Read-only: changes nothing.

        Returns:
            {"roll_no", "name", "dept", "room_number", "dues_due", "max_active_complaints", "active_complaints": [...]}.
        """
        s = self._student()
        return {
            "roll_no": s["roll_no"],
            "name": s["name"],
            "dept": s["dept"],
            "room_number": s["room_number"],
            "dues_due": s["dues_due"],
            "max_active_complaints": s["max_active_complaints"],
            "active_complaints": self.db.active_complaints(s["id"]),
        }

    def check_can_raise_complaint(self, issue: str) -> dict:
        """Check whether a new complaint can be raised for this student's room and issue under hostel policy rules.

        Use BEFORE raise_complaint, and whenever asking 'can I raise a complaint for X'. Decision is evaluated from the database policy table:
        never decide policy yourself. Read-only: changes nothing.

        Args:
            issue: The category of the issue (e.g. "Plumbing", "Electrical", "WiFi").

        Returns:
            {"can_raise": bool, "reasons": [str]}. Every reason is a business rule currently broken.
        """
        if not issue.strip():
            return {"error": "invalid_issue", "hint": "Provide a non-empty issue category."}

        s = self._student()
        reasons = []

        # Business Rule 1: One open complaint per room per issue
        existing = self.db.check_open_complaint(s["room_number"], issue)
        if existing:
            reasons.append(f"An open complaint (id #{existing['id']}) already exists for Room {s['room_number']} on issue '{issue}'")

        # Business Rule 2: Unpaid hostel dues limit
        dues_limit = self.db.policy("max_dues_to_raise_complaint")
        if s["dues_due"] > dues_limit:
            reasons.append(f"unpaid dues Rs {s['dues_due']} exceeds the Rs {dues_limit} limit")

        # Business Rule 3: Max active complaints per student
        active_count = len(self.db.active_complaints(s["id"]))
        if active_count >= s["max_active_complaints"]:
            reasons.append(f"already has {active_count} of {s['max_active_complaints']} allowed active complaints")

        return {"can_raise": not reasons, "reasons": reasons}

    def raise_complaint(self, issue: str, description: str) -> dict:
        """Raise a new complaint for the student's room. CHANGES DATA: inserts a new complaint record.

        Use only when student requests a complaint and check_can_raise_complaint allowed it.
        Safe to repeat: if already raised for the same room and issue, returns existing complaint.

        Args:
            issue: Issue category (e.g. "Plumbing", "Electrical", "WiFi").
            description: Detailed description of the problem.

        Returns:
            {"complaint_id", "status": "raised" | "already_open"}, or error: not_allowed with reasons.
        """
        verdict = self.check_can_raise_complaint(issue)
        if not verdict["can_raise"]:
            s = self._student()
            existing = self.db.check_open_complaint(s["room_number"], issue)
            if existing:
                return {"status": "already_open", "complaint_id": existing["id"]}
            return {"error": "not_allowed", "reasons": verdict["reasons"],
                    "hint": "Explain the reasons to the student. Do not retry."}

        if not description.strip():
            return {"error": "invalid_description", "hint": "Provide a clear description of the issue."}

        s = self._student()
        result = self.db.raise_complaint(s["id"], s["room_number"], issue, description)
        if isinstance(result, dict):
            return result
        return {"complaint_id": result, "status": "raised"}

    def assign_warden(self, complaint_id: int, warden_name: str) -> dict:
        """Assign a warden to an open complaint. CHANGES DATA: updates the complaint's assigned warden.

        Use when a complaint requires warden assignment or routing to staff.
        Safe to repeat: assigning the same warden to the same complaint returns existing status.

        Args:
            complaint_id: The integer ID of the complaint.
            warden_name: Name of the warden to assign (e.g. "Mr. Ramesh", "Mr. Suresh").

        Returns:
            {"complaint_id", "warden_assigned", "status": "assigned" | "already_assigned"}.
        """
        if not warden_name.strip():
            return {"error": "invalid_warden", "hint": "Pass a non-empty warden name."}

        return self.db.assign_warden(complaint_id, warden_name.strip())

    def notify_student(self, message: str) -> dict:
        """Send the student a short text notification. CHANGES DATA: queues an SMS text message.

        Use to confirm actions such as raising a complaint or warden assignment. The same message
        to the same student on the same day is sent only once. Never use to answer chat questions.

        Args:
            message: Text message (1 to 160 characters).

        Returns:
            {"notification_id", "status": "queued", "duplicate": bool}.
        """
        if not message.strip() or len(message) > 160:
            return {"error": "invalid_message", "hint": "Message must be 1 to 160 characters."}

        key = notification_dedupe_key(self.roll_no, message, self.clock().date())
        notification_id, created = self.db.record_notification(self.roll_no, message, key)
        return {"notification_id": notification_id, "status": "queued", "duplicate": not created}
