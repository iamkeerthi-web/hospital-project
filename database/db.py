import sqlite3
from contextlib import contextmanager
from flask import current_app, g


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    age INTEGER CHECK (age IS NULL OR (age >= 0 AND age <= 120)),
    phone TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    preferred_lang TEXT NOT NULL DEFAULT 'en',
    is_walkin_only INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    avg_consult_mins INTEGER NOT NULL DEFAULT 7 CHECK (avg_consult_mins >= 1),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    department_id INTEGER NOT NULL,
    phone TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    doctor_id INTEGER,
    appointment_time TEXT NOT NULL,
    symptoms TEXT NOT NULL,
    translated_symptoms TEXT NOT NULL,
    spoken_lang TEXT NOT NULL DEFAULT 'en',
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'checked_in', 'completed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT,
    FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_number TEXT NOT NULL UNIQUE,
    patient_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    doctor_id INTEGER,
    appointment_id INTEGER,
    token_type TEXT NOT NULL DEFAULT 'walk_in'
        CHECK (token_type IN ('walk_in', 'appointment')),
    symptoms TEXT NOT NULL,
    translated_symptoms TEXT NOT NULL,
    spoken_lang TEXT NOT NULL DEFAULT 'en',
    is_emergency INTEGER NOT NULL DEFAULT 0,
    priority_score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'waiting'
        CHECK (status IN ('waiting', 'checked_in', 'serving', 'completed', 'expired', 'cancelled')),
    checked_in_at TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT,
    FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE SET NULL,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL,
    token_id INTEGER NOT NULL UNIQUE,
    doctor_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    priority_level INTEGER NOT NULL DEFAULT 0,
    estimated_wait_mins INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'waiting'
        CHECK (status IN ('waiting', 'serving', 'completed', 'skipped', 'expired')),
    assigned_by_staff_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT,
    FOREIGN KEY (token_id) REFERENCES tokens(id) ON DELETE CASCADE,
    FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'staff')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone);
CREATE INDEX IF NOT EXISTS idx_doctors_dept ON doctors(department_id, is_available);
CREATE INDEX IF NOT EXISTS idx_tokens_dept_status ON tokens(department_id, status);
CREATE INDEX IF NOT EXISTS idx_tokens_patient ON tokens(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_queue_order ON queue(department_id, status, priority_level DESC, position ASC);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def get_db() -> sqlite3.Connection:
    """Return a per-request SQLite connection stored on Flask's g object."""
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_error=None) -> None:
    """Close the per-request SQLite connection if open."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def db_connection(db_path: str):
    """Context manager for database operations outside a Flask request context."""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Create all SQLite tables and indexes if they do not already exist."""
    with db_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
