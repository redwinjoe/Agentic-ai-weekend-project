# Hostel Complaints Desk Assistant: End-to-End Multi-Agent System

An end-to-end multi-agent assistant system for a Hostel Complaints Desk built with SQLite, Python, and Google Gemini API (or scripted models).

A student asks a question or reports an issue in natural language. A **supervisor agent** delegates work to two **specialist agents**:
- `info`: read-only specialist for checking room details and searching existing complaints.
- `desk`: action specialist bound to the student for logging complaints, assigning wardens, and queuing notifications.

```
student ─▶ queue (agent.db) ─▶ worker ─▶ supervisor ──ask_info──▶ info specialist ──▶ find_complaint, get_room
                                                   └─ask_desk──▶ desk specialist ──▶ get_student, check_can_raise_complaint,
                                                                                     raise_complaint*, assign_warden*, notify_student*
                                                                      * side effects: run once per key
```

## Business Rules in Data
- **One open complaint per room per issue**: Stored in the `policy` table (`max_open_complaints_per_room_per_issue = 1`). Enforced directly in database queries and transactions within `HostelDb.raise_complaint`, preventing duplicate complaints for the same room and issue category even if an LLM skips checks.
- **Unpaid Dues Policy**: Limit set in `policy` (`max_dues_to_raise_complaint = 500`).
- **Max Active Complaints**: Limit set in `policy` (`max_active_complaints_per_student = 3`).

## Run it (no API key needed)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m scripts.demo            # two questions, scripted models, every step printed
python -m scripts.demo --crash    # worker dies right after raising complaint; second worker finishes cleanly: PASS
pytest                            # full test suite (including crash replay, thread race test, policy checks)
```

With Gemini (`export GEMINI_API_KEY=...` or `set GEMINI_API_KEY=...`):

```bash
python -m scripts.demo --real                                      # same questions, real Gemini models
python -m scripts.worker                                           # terminal 1
python -m scripts.ask --student 22CS045 "My tap is leaking in Room 101, please raise a complaint."   # terminal 2
```

## Seed Data

| Student | Room | Dues | Max Complaints | Status / Behaviour |
|---|---|---|---|---|
| 22CS045 Priya Raman | 101 | Rs 0 | 3 | Can log complaint & get warden assigned |
| 22IT017 Arjun Kumar | 102 | Rs 600 | 3 | Refused: unpaid dues above Rs 500 policy |
| 22EC031 Divya Sekar | 103 | Rs 0 | 1 | Refused: Room 103 already has an open complaint for Plumbing |

Wardens:
- Mr. Ramesh (Plumbing & Sanitation)
- Mr. Suresh (Electrical & Maintenance)
- Ms. Lakshmi (General & WiFi)

## Key Architecture & Requirements Fulfilled
1. **Two Databases**: `agent.db` for thread/run queue memory; `hostel.db` for domain business data.
2. **6 Tools with Detailed Descriptions**: `find_complaint`, `get_room`, `get_student`, `check_can_raise_complaint`, `raise_complaint`, `assign_warden`, `notify_student`. Every description states when to use, when not to, and what it changes.
3. **Business Rule in Data**: Policy table stores constraints. `raise_complaint` enforces the rule in SQL even if prompt instructions are skipped.
4. **Queue & Worker with Lease**: Background worker claims run with a lease time; dead worker runs are recovered by standard reaper/reclaim logic.
5. **Idempotency**: All side effects run through unique keys stored in `idempotency` and `notification` tables.
6. **Multi-agent Security & Least Privilege**: `info` specialist has zero write tools. `desk` specialist is strictly scoped to one student's roll number.
7. **Proof without API Key**: `scripts.demo` runs end-to-end with scripted models. `python -m scripts.demo --crash` outputs `PASS`. Pytest suite passes 21 tests.
8. **Thread Safety & Race Test**: `test_thread_race_condition` verifies that multiple concurrent threads trying to raise a complaint for the same room and issue only result in 1 raised complaint.
