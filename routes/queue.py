from flask import Blueprint, g, jsonify

from database.db import get_db
from services.queue_service import (
    recalculate_all_queues,
    recalculate_department_queue,
)
from utils.security import require_auth
from utils.validators import clean_str, get_json_body

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")

ALLOWED_QUEUE_STATUSES = {"waiting", "serving", "completed", "skipped"}


def _serialize_queue_item(row) -> dict:
    item = dict(row)
    item["is_emergency"] = bool(item.get("is_emergency", 0))
    item["checked_in"] = bool(item.get("checked_in_at"))
    return item


def _department_queue_snapshot(db, dept_id: int) -> dict | None:
    dept = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not dept:
        return None

    recalculate_department_queue(db, dept_id)

    available_doctors = db.execute(
        "SELECT id, name, specialization FROM doctors WHERE department_id = ? AND is_available = 1",
        (dept_id,),
    ).fetchall()

    serving = db.execute(
        """
        SELECT q.*, t.token_number, t.token_type, t.symptoms, t.translated_symptoms,
               t.is_emergency, t.checked_in_at, t.expires_at, t.status AS token_status,
               p.name AS patient_name, p.age AS patient_age, p.phone AS patient_phone,
               doc.name AS doctor_name
        FROM queue q
        JOIN tokens t ON t.id = q.token_id
        JOIN patients p ON p.id = t.patient_id
        LEFT JOIN doctors doc ON doc.id = q.doctor_id
        WHERE q.department_id = ? AND q.status = 'serving'
        ORDER BY q.updated_at DESC
        """,
        (dept_id,),
    ).fetchall()

    waiting = db.execute(
        """
        SELECT q.*, t.token_number, t.token_type, t.symptoms, t.translated_symptoms,
               t.is_emergency, t.checked_in_at, t.expires_at, t.status AS token_status,
               p.name AS patient_name, p.age AS patient_age, p.phone AS patient_phone,
               doc.name AS doctor_name
        FROM queue q
        JOIN tokens t ON t.id = q.token_id
        JOIN patients p ON p.id = t.patient_id
        LEFT JOIN doctors doc ON doc.id = q.doctor_id
        WHERE q.department_id = ? AND q.status = 'waiting'
        ORDER BY q.priority_level DESC, q.id ASC
        """,
        (dept_id,),
    ).fetchall()

    return {
        "department": dict(dept),
        "available_doctors": [dict(d) for d in available_doctors],
        "available_doctor_count": len(available_doctors),
        "current_serving": [_serialize_queue_item(s) for s in serving],
        "current_serving_token": serving[0]["token_number"] if serving else None,
        "waiting_count": len(waiting),
        "waiting_list": [_serialize_queue_item(w) for w in waiting],
    }


@queue_bp.route("/overview", methods=["GET"])
def queue_overview():
    db = get_db()
    recalculate_all_queues(db)
    depts = db.execute("SELECT id FROM departments WHERE is_active = 1 ORDER BY id ASC").fetchall()

    overview = []
    for d in depts:
        snap = _department_queue_snapshot(db, d["id"])
        if snap:
            overview.append({
                "department_id": snap["department"]["id"],
                "department_name": snap["department"]["name"],
                "department_code": snap["department"]["code"],
                "avg_consult_mins": snap["department"]["avg_consult_mins"],
                "available_doctor_count": snap["available_doctor_count"],
                "current_serving_token": snap["current_serving_token"],
                "waiting_count": snap["waiting_count"],
                "emergency_count": sum(1 for item in snap["waiting_list"] if item["is_emergency"]),
            })

    return jsonify({"departments": overview}), 200


@queue_bp.route("/department/<int:dept_id>", methods=["GET"])
def get_department_queue(dept_id: int):
    db = get_db()
    snap = _department_queue_snapshot(db, dept_id)
    if not snap:
        return jsonify({"error": "Department not found"}), 404
    return jsonify(snap), 200


@queue_bp.route("/position/<string:token_number>", methods=["GET"])
def get_token_position(token_number: str):
    db = get_db()
    row = db.execute(
        """
        SELECT t.*, d.name AS department_name, d.code AS department_code,
               q.position, q.estimated_wait_mins, q.priority_level, q.status AS queue_status
        FROM tokens t
        JOIN departments d ON d.id = t.department_id
        LEFT JOIN queue q ON q.token_id = t.id
        WHERE upper(t.token_number) = ?
        """,
        (clean_str(token_number).upper(),),
    ).fetchone()
    if not row:
        return jsonify({"error": "Token not found"}), 404

    recalculate_department_queue(db, row["department_id"])

    refreshed = db.execute(
        """
        SELECT t.token_number, t.status AS token_status, t.is_emergency, t.expires_at, t.checked_in_at,
               d.id AS department_id, d.name AS department_name, d.code AS department_code,
               q.position, q.estimated_wait_mins, q.priority_level, q.status AS queue_status
        FROM tokens t
        JOIN departments d ON d.id = t.department_id
        LEFT JOIN queue q ON q.token_id = t.id
        WHERE t.id = ?
        """,
        (row["id"],),
    ).fetchone()

    serving = db.execute(
        """
        SELECT t.token_number
        FROM queue q
        JOIN tokens t ON t.id = q.token_id
        WHERE q.department_id = ? AND q.status = 'serving'
        ORDER BY q.updated_at DESC LIMIT 1
        """,
        (row["department_id"],),
    ).fetchone()

    waiting_count = db.execute(
        "SELECT COUNT(*) FROM queue WHERE department_id = ? AND status = 'waiting'",
        (row["department_id"],),
    ).fetchone()[0]

    pos = refreshed["position"] if refreshed["queue_status"] == "waiting" else 0
    return jsonify({
        "token_number": refreshed["token_number"],
        "token_status": refreshed["token_status"],
        "queue_status": refreshed["queue_status"],
        "department_id": refreshed["department_id"],
        "department_name": refreshed["department_name"],
        "department_code": refreshed["department_code"],
        "is_emergency": bool(refreshed["is_emergency"]),
        "position": pos,
        "patients_ahead": max(0, pos - 1) if pos else 0,
        "estimated_wait_mins": refreshed["estimated_wait_mins"] if pos else 0,
        "current_serving_token": serving["token_number"] if serving else None,
        "waiting_count": waiting_count,
        "checked_in": bool(refreshed["checked_in_at"]),
        "expires_at": refreshed["expires_at"],
    }), 200


@queue_bp.route("/department/<int:dept_id>/next", methods=["POST"])
@require_auth(["doctor", "staff", "admin"])
def call_next_patient(dept_id: int):
    db = get_db()
    dept = db.execute("SELECT id FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not dept:
        return jsonify({"error": "Department not found"}), 404

    recalculate_department_queue(db, dept_id)

    # Complete any currently serving token in this department
    serving_rows = db.execute(
        "SELECT token_id FROM queue WHERE department_id = ? AND status = 'serving'",
        (dept_id,),
    ).fetchall()
    for s in serving_rows:
        db.execute(
            "UPDATE queue SET status = 'completed', position = 0, estimated_wait_mins = 0, updated_at = datetime('now') WHERE token_id = ?",
            (s["token_id"],),
        )
        db.execute(
            "UPDATE tokens SET status = 'completed', updated_at = datetime('now') WHERE id = ?",
            (s["token_id"],),
        )
        db.execute(
            """
            UPDATE appointments
            SET status = 'completed', updated_at = datetime('now')
            WHERE id = (SELECT appointment_id FROM tokens WHERE id = ?)
            """,
            (s["token_id"],),
        )

    next_entry = db.execute(
        """
        SELECT q.id AS queue_id, q.token_id, t.token_number
        FROM queue q
        JOIN tokens t ON t.id = q.token_id
        WHERE q.department_id = ? AND q.status = 'waiting'
        ORDER BY q.priority_level DESC, q.id ASC
        LIMIT 1
        """,
        (dept_id,),
    ).fetchone()

    if not next_entry:
        db.commit()
        return jsonify({
            "message": "No patients waiting in this department queue",
            "serving": None,
        }), 200

    doctor_id = g.current_user["sub"] if g.current_user["role"] == "doctor" else None
    db.execute(
        """
        UPDATE queue
        SET status = 'serving', position = 0, estimated_wait_mins = 0,
            doctor_id = COALESCE(?, doctor_id), updated_at = datetime('now')
        WHERE id = ?
        """,
        (doctor_id, next_entry["queue_id"]),
    )
    db.execute(
        """
        UPDATE tokens
        SET status = 'serving', doctor_id = COALESCE(?, doctor_id), updated_at = datetime('now')
        WHERE id = ?
        """,
        (doctor_id, next_entry["token_id"]),
    )
    db.commit()

    snap = _department_queue_snapshot(db, dept_id)
    return jsonify({
        "message": f"Now serving token {next_entry['token_number']}",
        "serving": snap["current_serving"][0] if snap["current_serving"] else None,
        "queue": snap,
    }), 200


@queue_bp.route("/<int:token_id>/status", methods=["PATCH"])
@require_auth(["doctor", "staff", "admin"])
def update_queue_status(token_id: int):
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    new_status = clean_str(data.get("status")).lower()
    if new_status not in ALLOWED_QUEUE_STATUSES:
        return jsonify({
            "error": f"status must be one of: {', '.join(sorted(ALLOWED_QUEUE_STATUSES))}"
        }), 400

    db = get_db()
    entry = db.execute(
        "SELECT q.*, t.appointment_id FROM queue q JOIN tokens t ON t.id = q.token_id WHERE q.token_id = ?",
        (token_id,),
    ).fetchone()
    if not entry:
        return jsonify({"error": "Queue entry not found for token"}), 404

    token_status = "cancelled" if new_status == "skipped" else new_status
    pos_sql = "" if new_status == "waiting" else ", position = 0, estimated_wait_mins = 0"

    db.execute(
        f"UPDATE queue SET status = ?{pos_sql}, updated_at = datetime('now') WHERE token_id = ?",
        (new_status, token_id),
    )
    db.execute(
        "UPDATE tokens SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (token_status, token_id),
    )
    if entry["appointment_id"] and new_status == "completed":
        db.execute(
            "UPDATE appointments SET status = 'completed', updated_at = datetime('now') WHERE id = ?",
            (entry["appointment_id"],),
        )
    db.commit()

    snap = _department_queue_snapshot(db, entry["department_id"])
    return jsonify({
        "message": f"Token queue status updated to {new_status}",
        "queue": snap,
    }), 200


@queue_bp.route("/<int:token_id>/priority", methods=["PATCH"])
@require_auth(["doctor", "staff", "admin"])
def assign_emergency_priority(token_id: int):
    """Allow authorized staff/doctors/admins to assign emergency priority and reorder queue."""
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    is_emergency = bool(data.get("is_emergency", True))
    default_level = 10 if is_emergency else 0
    try:
        priority_level = int(data.get("priority_level", default_level))
    except (TypeError, ValueError):
        return jsonify({"error": "priority_level must be an integer"}), 400

    if priority_level < 0 or priority_level > 100:
        return jsonify({"error": "priority_level must be between 0 and 100"}), 400
    if is_emergency and priority_level == 0:
        priority_level = 10

    db = get_db()
    entry = db.execute(
        "SELECT q.*, t.token_number FROM queue q JOIN tokens t ON t.id = q.token_id WHERE q.token_id = ?",
        (token_id,),
    ).fetchone()
    if not entry:
        return jsonify({"error": "Queue entry not found"}), 404
    if entry["status"] not in ("waiting", "serving"):
        return jsonify({"error": f"Cannot reprioritize token in '{entry['status']}' state"}), 400

    staff_id = g.current_user["sub"] if g.current_user["role"] in ("admin", "staff") else None

    db.execute(
        """
        UPDATE queue
        SET priority_level = ?, assigned_by_staff_id = ?, updated_at = datetime('now')
        WHERE token_id = ?
        """,
        (priority_level, staff_id, token_id),
    )
    db.execute(
        """
        UPDATE tokens
        SET is_emergency = ?, priority_score = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (int(is_emergency), priority_level, token_id),
    )
    db.commit()

    snap = _department_queue_snapshot(db, entry["department_id"])
    return jsonify({
        "message": f"Priority updated for token {entry['token_number']}",
        "token_id": token_id,
        "is_emergency": is_emergency,
        "priority_level": priority_level,
        "queue": snap,
    }), 200
