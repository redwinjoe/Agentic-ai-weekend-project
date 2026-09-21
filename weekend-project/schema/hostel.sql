-- hostel.db: hostel complaints, rooms, students, wardens, policy rules.

CREATE TABLE IF NOT EXISTS student (
    id                     INTEGER PRIMARY KEY,
    roll_no                TEXT NOT NULL UNIQUE,
    name                   TEXT NOT NULL,
    dept                   TEXT NOT NULL,
    room_number            TEXT NOT NULL,
    max_active_complaints  INTEGER NOT NULL DEFAULT 3,
    dues_due               INTEGER NOT NULL DEFAULT 0 CHECK (dues_due >= 0)
);

CREATE TABLE IF NOT EXISTS room (
    id           INTEGER PRIMARY KEY,
    room_number  TEXT NOT NULL UNIQUE,
    hostel_block TEXT NOT NULL,
    floor        INTEGER NOT NULL,
    capacity     INTEGER NOT NULL DEFAULT 2,
    occupied     INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS warden (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    specialty  TEXT NOT NULL,
    contact    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS complaint (
    id               INTEGER PRIMARY KEY,
    student_id       INTEGER NOT NULL REFERENCES student (id),
    room_number      TEXT NOT NULL,
    issue            TEXT NOT NULL,
    description      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('open', 'assigned', 'in_progress', 'resolved')),
    warden_assigned  TEXT,
    created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS policy (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notification (
    id          INTEGER PRIMARY KEY,
    roll_no     TEXT NOT NULL,
    message     TEXT NOT NULL,
    dedupe_key  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
    key         TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
