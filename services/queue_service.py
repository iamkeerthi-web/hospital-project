from datetime import datetime, timedelta, timezone

EMERGENCY_KEYWORDS = (
    "pain", "breath", "bleeding", "chest", "heart", "unconscious", "accident", "critical", "serious",
    "dard", "saans", "khoon", "seene", "behoshi", "gir", "dil", "chakkar",
    "nōvu", "ushu", "usiru", "ede", "hr̥daya", "raktasrāva", "pramāda",
    "vali", "mūccu", "neñcu", "irutaya", "vativu", "avacara",
    "nōppī", "ūpiri", "chāti", "hr̥dayaṁ", "raktasrāvaṁ",
    "ocd", "anxiety", "panic", "depression",
)


def detect_emergency(text: str) -> bool:
    """Detect emergency keywords across supported languages."""
    lower = (text or "").lower()
    return any(keyword in lower for keyword in EMERGENCY_KEYWORDS)


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def expiry_str(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def generate_token_number(db, department_id: int, dept_code: str, is_emergency: bool) -> str:
    """Generate a unique, human-readable token number per department."""
    prefix = f"{dept_code}-EMG" if is_emergency else dept_code
    rows = db.execute(
        "SELECT token_number FROM tokens WHERE token_number LIKE ?",
        (f"{prefix}-%",),
    ).fetchall()

    max_seq = 0
    for row in rows:
        parts = row["token_number"].rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            max_seq = max(max_seq, int(parts[1]))

    return f"{prefix}-{max_seq + 1:03d}"


def cleanup_expired_tokens(db) -> int:
    """Mark expired unchecked-in tokens as expired and return count updated."""
    now = utc_now_str()
    expired_rows = db.execute(
        """
        SELECT id, department_id
        FROM tokens
        WHERE status = 'waiting'
          AND checked_in_at IS NULL
          AND expires_at <= ?
        """,
        (now,),
    ).fetchall()

    if not expired_rows:
        return 0

    token_ids = [row["id"] for row in expired_rows]
    placeholders = ",".join("?" for _ in token_ids)

    db.execute(
        f"""
        UPDATE tokens
        SET status = 'expired', updated_at = datetime('now')
        WHERE id IN ({placeholders})
        """,
        token_ids,
    )
    db.execute(
        f"""
        UPDATE queue
        SET status = 'expired', position = 0, estimated_wait_mins = 0, updated_at = datetime('now')
        WHERE token_id IN ({placeholders})
        """,
        token_ids,
    )
    db.commit()
    return len(token_ids)


def recalculate_department_queue(db, department_id: int) -> list[dict]:
    """Recalculate queue positions and estimated wait times for a department."""
    cleanup_expired_tokens(db)

    dept = db.execute(
        "SELECT id, avg_consult_mins FROM departments WHERE id = ?",
        (department_id,),
    ).fetchone()
    avg_consult = dept["avg_consult_mins"] if dept else 7

    available_doctors = db.execute(
        "SELECT COUNT(*) FROM doctors WHERE department_id = ? AND is_available = 1",
        (department_id,),
    ).fetchone()[0]
    effective_doctors = max(1, available_doctors)

    waiting_rows = db.execute(
        """
        SELECT q.id AS queue_id, q.token_id, q.priority_level, t.is_emergency
        FROM queue q
        JOIN tokens t ON t.id = q.token_id
        WHERE q.department_id = ? AND q.status = 'waiting'
        ORDER BY q.priority_level DESC, q.id ASC
        """,
        (department_id,),
    ).fetchall()

    normal_ahead = 0
    emergency_ahead = 0
    updated_items = []

    for index, row in enumerate(waiting_rows, start=1):
        is_emg = bool(row["is_emergency"] or row["priority_level"] > 0)
        if is_emg:
            est_wait = max(1, (emergency_ahead * 3) + 3)
            emergency_ahead += 1
        else:
            normal_ahead += 1
            est_wait = max(1, round((normal_ahead * avg_consult) / effective_doctors))

        db.execute(
            """
            UPDATE queue
            SET position = ?, estimated_wait_mins = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (index, est_wait, row["queue_id"]),
        )
        updated_items.append({
            "queue_id": row["queue_id"],
            "token_id": row["token_id"],
            "position": index,
            "estimated_wait_mins": est_wait,
        })

    db.commit()
    return updated_items


def recalculate_all_queues(db) -> None:
    """Cleanup expired tokens and recalculate positions for every active department."""
    cleanup_expired_tokens(db)
    depts = db.execute("SELECT id FROM departments WHERE is_active = 1").fetchall()
    for dept in depts:
        recalculate_department_queue(db, dept["id"])
