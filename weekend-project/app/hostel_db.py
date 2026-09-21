"""hostel.db: students, rooms, complaints, wardens. Every SQL statement for hostel domain lives here."""
import json
import time
from collections.abc import Callable
from pathlib import Path

from app.db import connect, transaction

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "hostel.sql"


class HostelDb:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time):
        self.conn = connect(path)
        self.clock = clock

    def transaction(self):
        return transaction(self.conn)

    def migrate(self) -> None:
        self.conn.executescript(SCHEMA.read_text())
        if self.conn.execute("SELECT count(*) FROM student").fetchone()[0]:
            return
        with self.transaction() as c:
            c.executemany("INSERT INTO student VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (1, "22CS045", "Priya Raman", "CSE", "101", 3, 0),
                (2, "22IT017", "Arjun Kumar", "IT", "102", 3, 600),
                (3, "22EC031", "Divya Sekar", "ECE", "103", 1, 0),
            ])
            c.executemany("INSERT INTO room VALUES (?, ?, ?, ?, ?, ?)", [
                (1, "101", "Block A", 1, 2, 2),
                (2, "102", "Block A", 1, 2, 2),
                (3, "103", "Block B", 1, 2, 2),
            ])
            c.executemany("INSERT INTO warden VALUES (?, ?, ?, ?)", [
                (1, "Mr. Ramesh", "Plumbing & Sanitation", "9876543210"),
                (2, "Mr. Suresh", "Electrical & Maintenance", "9876543211"),
                (3, "Ms. Lakshmi", "General & WiFi", "9876543212"),
            ])
            c.executemany("INSERT INTO policy VALUES (?, ?)", [
                ("max_open_complaints_per_room_per_issue", 1),
                ("max_active_complaints_per_student", 3),
                ("max_dues_to_raise_complaint", 500),
            ])
            # Seed 1 active open complaint for Room 103 (Divya Sekar)
            c.execute(
                "INSERT INTO complaint (id, student_id, room_number, issue, description, status, warden_assigned, created_at)"
                " VALUES (1, 3, '103', 'Plumbing', 'Bathroom tap leaking in room 103', 'open', 'Mr. Ramesh', ?)",
                (self.clock(),)
            )

    # ------------------------------------------------------------------ reads

    def get_student(self, roll_no: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM student WHERE roll_no = ?", (roll_no,)).fetchone()
        return dict(r) if r else None

    def policy(self, name: str) -> int:
        row = self.conn.execute("SELECT value FROM policy WHERE name = ?", (name,)).fetchone()
        if not row:
            raise KeyError(f"Policy '{name}' not found")
        return row[0]

    def active_complaints(self, student_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, room_number, issue, description, status, warden_assigned, created_at"
            " FROM complaint WHERE student_id = ? AND status IN ('open', 'assigned', 'in_progress')"
            " ORDER BY id", (student_id,)).fetchall()
        return [dict(r) for r in rows]

    def find_complaints(self, text: str, limit: int = 5) -> list[dict]:
        like = f"%{text.strip()}%"
        rows = self.conn.execute(
            "SELECT c.id, c.room_number, c.issue, c.description, c.status, c.warden_assigned, s.name as resident_name"
            " FROM complaint c JOIN student s ON s.id = c.student_id"
            " WHERE c.room_number LIKE ? OR c.issue LIKE ? OR c.status LIKE ? OR c.description LIKE ?"
            " ORDER BY c.id DESC LIMIT ?",
            (like, like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_room(self, room_number: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM room WHERE room_number = ?", (room_number,)).fetchone()
        if not r:
            return None
        room_dict = dict(r)
        residents = self.conn.execute(
            "SELECT id, roll_no, name, dept, dues_due FROM student WHERE room_number = ?", (room_number,)).fetchall()
        room_dict["residents"] = [dict(res) for res in residents]
        active = self.conn.execute(
            "SELECT id, issue, status, warden_assigned FROM complaint WHERE room_number = ? AND status IN ('open', 'assigned', 'in_progress')",
            (room_number,)).fetchall()
        room_dict["active_complaints"] = [dict(a) for a in active]
        return room_dict

    def get_complaint(self, complaint_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM complaint WHERE id = ?", (complaint_id,)).fetchone()
        return dict(r) if r else None

    def check_open_complaint(self, room_number: str, issue: str) -> dict | None:
        """Returns existing open complaint if any for this room and issue category."""
        r = self.conn.execute(
            "SELECT * FROM complaint WHERE room_number = ? AND LOWER(issue) = LOWER(?)"
            " AND status IN ('open', 'assigned', 'in_progress')",
            (room_number, issue)).fetchone()
        return dict(r) if r else None

    def count(self, table: str) -> int:
        assert table.isidentifier()
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    # ------------------------------------------------------------------ safe writes

    def raise_complaint(self, student_id: int, room_number: str, issue: str, description: str) -> str | dict:
        """Raise a complaint for a room & issue. Safe to repeat, enforces policy in data."""
        with self.transaction() as c:
            existing = c.execute(
                "SELECT id, status FROM complaint WHERE room_number = ? AND LOWER(issue) = LOWER(?)"
                " AND status IN ('open', 'assigned', 'in_progress')",
                (room_number, issue)).fetchone()
            if existing:
                return {"status": "already_open", "complaint_id": existing["id"]}

            cur = c.execute(
                "INSERT INTO complaint (student_id, room_number, issue, description, status, created_at)"
                " VALUES (?, ?, ?, ?, 'open', ?)",
                (student_id, room_number, issue.title(), description, self.clock()))
            complaint_id = cur.lastrowid
            return {"status": "raised", "complaint_id": complaint_id}

    def assign_warden(self, complaint_id: int, warden_name: str) -> dict:
        """Assign a warden to a complaint. Safe to repeat."""
        with self.transaction() as c:
            comp = c.execute("SELECT * FROM complaint WHERE id = ?", (complaint_id,)).fetchone()
            if not comp:
                return {"error": "unknown_complaint", "hint": "Check complaint_id."}
            if comp["warden_assigned"] == warden_name and comp["status"] in ('assigned', 'in_progress'):
                return {"complaint_id": complaint_id, "warden_assigned": warden_name, "status": "already_assigned"}

            c.execute(
                "UPDATE complaint SET warden_assigned = ?, status = 'assigned' WHERE id = ?",
                (warden_name, complaint_id))
            return {"complaint_id": complaint_id, "warden_assigned": warden_name, "status": "assigned"}

    def record_notification(self, roll_no: str, message: str, dedupe_key: str) -> tuple[int, bool]:
        cur = self.conn.execute(
            "INSERT INTO notification (roll_no, message, dedupe_key, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (dedupe_key) DO NOTHING", (roll_no, message, dedupe_key, self.clock()))
        if cur.rowcount == 1:
            return cur.lastrowid, True
        return self.conn.execute("SELECT id FROM notification WHERE dedupe_key = ?", (dedupe_key,)).fetchone()[0], False

    def once(self, key: str, tool_name: str, effect: Callable[[], dict]) -> tuple[dict, bool]:
        """Run a side effect at most once per idempotency key; the effect and its key commit together."""
        with self.transaction() as c:
            row = c.execute("SELECT result FROM idempotency WHERE key = ?", (key,)).fetchone()
            if row is not None:
                return json.loads(row["result"]), False
            result = effect()
            c.execute("INSERT INTO idempotency (key, tool_name, result, created_at) VALUES (?, ?, ?, ?)",
                      (key, tool_name, json.dumps(result, default=str), self.clock()))
            return result, True
