from database.db import db_connection
from utils.security import hash_password


DEFAULT_DEPARTMENTS = [
    ("General Medicine", "GEN", "Primary care and general consultations", 7),
    ("Cardiology", "CARD", "Heart and cardiovascular care", 10),
    ("Orthopedics", "ORTH", "Bones, joints, and trauma care", 8),
    ("Pediatrics", "PED", "Child and infant healthcare", 7),
    ("Emergency", "EMG", "Immediate triage and critical care", 3),
]

DEFAULT_DOCTORS = [
    ("Dr. Aarav Sharma", "General Physician", "GEN", "9000000001", "aarav.sharma@smartqueue.local", "Doctor@123", 1),
    ("Dr. Priya Nair", "Cardiologist", "CARD", "9000000002", "priya.nair@smartqueue.local", "Doctor@123", 1),
    ("Dr. Rohan Kulkarni", "Orthopedic Surgeon", "ORTH", "9000000003", "rohan.kulkarni@smartqueue.local", "Doctor@123", 1),
    ("Dr. Meera Iyer", "Pediatrician", "PED", "9000000004", "meera.iyer@smartqueue.local", "Doctor@123", 1),
    ("Dr. Vikram Rao", "Emergency Medicine", "EMG", "9000000005", "vikram.rao@smartqueue.local", "Doctor@123", 1),
]


def seed_data(db_path: str, config) -> None:
    """Populate initial departments, doctors, and admin/staff accounts if empty."""
    with db_connection(db_path) as conn:
        dept_count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if dept_count == 0:
            conn.executemany(
                """
                INSERT INTO departments (name, code, description, avg_consult_mins)
                VALUES (?, ?, ?, ?)
                """,
                DEFAULT_DEPARTMENTS,
            )

        doc_count = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
        if doc_count == 0:
            dept_rows = conn.execute("SELECT id, code FROM departments").fetchall()
            dept_by_code = {row["code"]: row["id"] for row in dept_rows}
            for name, spec, dept_code, phone, email, raw_pw, available in DEFAULT_DOCTORS:
                dept_id = dept_by_code.get(dept_code)
                if dept_id is not None:
                    conn.execute(
                        """
                        INSERT INTO doctors (
                            name, specialization, department_id, phone, email, password_hash, is_available
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (name, spec, dept_id, phone, email, hash_password(raw_pw), available),
                    )

        admin_count = conn.execute("SELECT COUNT(*) FROM admin").fetchone()[0]
        if admin_count == 0:
            conn.execute(
                """
                INSERT INTO admin (username, full_name, email, password_hash, role)
                VALUES (?, ?, ?, ?, 'admin')
                """,
                (
                    config["DEFAULT_ADMIN_USERNAME"],
                    "System Administrator",
                    config["DEFAULT_ADMIN_EMAIL"],
                    hash_password(config["DEFAULT_ADMIN_PASSWORD"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO admin (username, full_name, email, password_hash, role)
                VALUES (?, ?, ?, ?, 'staff')
                """,
                (
                    config["DEFAULT_STAFF_USERNAME"],
                    "Front Desk Staff",
                    "staff@smartqueue.local",
                    hash_password(config["DEFAULT_STAFF_PASSWORD"]),
                ),
            )
