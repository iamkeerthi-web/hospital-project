import sqlite3
from flask import Blueprint, jsonify, request

from database.db import get_db
from services.queue_service import recalculate_all_queues
from utils.security import (
    create_auth_token,
    hash_password,
    require_auth,
    verify_password,
)
from utils.validators import (
    clean_str,
    get_json_body,
    validate_email,
    validate_password,
    validate_phone,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.route("/login", methods=["POST"])
def admin_login():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    identifier = clean_str(data.get("username") or data.get("identifier") or data.get("email")).lower()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Username/email and password are required"}), 400

    db = get_db()
    account = db.execute(
        "SELECT * FROM admin WHERE lower(username) = ? OR lower(email) = ?",
        (identifier, identifier),
    ).fetchone()

    if not account or not verify_password(password, account["password_hash"]):
        return jsonify({"error": "Invalid admin/staff credentials"}), 401

    token = create_auth_token(
        account["id"],
        account["role"],
        {"username": account["username"], "full_name": account["full_name"]},
    )
    return jsonify({
        "message": "Login successful",
        "auth_token": token,
        "user": {
            "id": account["id"],
            "username": account["username"],
            "full_name": account["full_name"],
            "email": account["email"],
            "role": account["role"],
        },
    }), 200


@admin_bp.route("/patients", methods=["GET"])
@require_auth(["admin", "staff"])
def list_all_patients():
    query = clean_str(request.args.get("q")).lower()
    db = get_db()

    if query:
        like_q = f"%{query}%"
        rows = db.execute(
            """
            SELECT p.id, p.name, p.age, p.phone, p.email, p.preferred_lang,
                   p.is_walkin_only, p.created_at,
                   COUNT(t.id) AS total_tokens
            FROM patients p
            LEFT JOIN tokens t ON t.patient_id = p.id
            WHERE lower(p.name) LIKE ? OR p.phone LIKE ? OR lower(COALESCE(p.email, '')) LIKE ?
            GROUP BY p.id
            ORDER BY p.created_at DESC, p.id DESC
            """,
            (like_q, like_q, like_q),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT p.id, p.name, p.age, p.phone, p.email, p.preferred_lang,
                   p.is_walkin_only, p.created_at,
                   COUNT(t.id) AS total_tokens
            FROM patients p
            LEFT JOIN tokens t ON t.patient_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC, p.id DESC
            """
        ).fetchall()

    return jsonify({"patients": [dict(r) for r in rows]}), 200


@admin_bp.route("/doctors", methods=["POST"])
@require_auth(["admin"])
def admin_create_doctor():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    name = clean_str(data.get("name"))
    specialization = clean_str(data.get("specialization"))
    email = clean_str(data.get("email")).lower()
    phone = clean_str(data.get("phone")) or None
    password = data.get("password") or "Doctor@123"
    is_available = int(bool(data.get("is_available", True)))

    try:
        department_id = int(data.get("department_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valid department_id is required"}), 400

    if not name or not specialization:
        return jsonify({"error": "Doctor name and specialization are required"}), 400
    if not email or not validate_email(email):
        return jsonify({"error": "Valid email is required"}), 400
    if phone and not validate_phone(phone):
        return jsonify({"error": "Phone must be a valid 10-digit number"}), 400
    pw_err = validate_password(password)
    if pw_err:
        return jsonify({"error": pw_err}), 400

    db = get_db()
    dept = db.execute("SELECT id FROM departments WHERE id = ?", (department_id,)).fetchone()
    if not dept:
        return jsonify({"error": "Department not found"}), 404

    try:
        cursor = db.execute(
            """
            INSERT INTO doctors (name, specialization, department_id, phone, email, password_hash, is_available)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, specialization, department_id, phone, email, hash_password(password), is_available),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Doctor with this email or phone already exists"}), 409

    doctor = db.execute(
        """
        SELECT doc.id, doc.name, doc.specialization, doc.department_id,
               d.name AS department_name, d.code AS department_code,
               doc.phone, doc.email, doc.is_available, doc.created_at
        FROM doctors doc
        JOIN departments d ON d.id = doc.department_id
        WHERE doc.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    result = dict(doctor)
    result["is_available"] = bool(result["is_available"])
    return jsonify({"message": "Doctor added", "doctor": result}), 201


@admin_bp.route("/doctors/<int:doctor_id>", methods=["DELETE"])
@require_auth(["admin"])
def admin_delete_doctor(doctor_id: int):
    db = get_db()
    doctor = db.execute("SELECT id, name FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    if not doctor:
        return jsonify({"error": "Doctor not found"}), 404

    active_serving = db.execute(
        "SELECT COUNT(*) FROM queue WHERE doctor_id = ? AND status = 'serving'",
        (doctor_id,),
    ).fetchone()[0]
    if active_serving > 0:
        return jsonify({"error": "Cannot delete a doctor currently serving a patient"}), 409

    db.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,))
    db.commit()
    return jsonify({"message": f"Doctor {doctor['name']} removed"}), 200


@admin_bp.route("/departments/<int:dept_id>", methods=["DELETE"])
@require_auth(["admin"])
def admin_deactivate_department(dept_id: int):
    db = get_db()
    dept = db.execute("SELECT id, name FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not dept:
        return jsonify({"error": "Department not found"}), 404

    active_waiting = db.execute(
        "SELECT COUNT(*) FROM queue WHERE department_id = ? AND status IN ('waiting', 'serving')",
        (dept_id,),
    ).fetchone()[0]
    if active_waiting > 0:
        return jsonify({"error": "Cannot deactivate department with active waiting or serving tokens"}), 409

    db.execute("UPDATE departments SET is_active = 0 WHERE id = ?", (dept_id,))
    db.commit()
    return jsonify({"message": f"Department {dept['name']} deactivated"}), 200


@admin_bp.route("/queues", methods=["GET"])
@require_auth(["admin", "staff"])
def monitor_all_queues():
    db = get_db()
    recalculate_all_queues(db)

    depts = db.execute("SELECT * FROM departments WHERE is_active = 1 ORDER BY id ASC").fetchall()
    result = []
    for dept in depts:
        items = db.execute(
            """
            SELECT q.*, t.token_number, t.token_type, t.symptoms, t.translated_symptoms,
                   t.is_emergency, t.checked_in_at, t.expires_at, t.status AS token_status,
                   p.name AS patient_name, p.phone AS patient_phone,
                   doc.name AS doctor_name
            FROM queue q
            JOIN tokens t ON t.id = q.token_id
            JOIN patients p ON p.id = t.patient_id
            LEFT JOIN doctors doc ON doc.id = q.doctor_id
            WHERE q.department_id = ? AND q.status IN ('serving', 'waiting')
            ORDER BY CASE WHEN q.status = 'serving' THEN 0 ELSE 1 END,
                     q.priority_level DESC, q.id ASC
            """,
            (dept["id"],),
        ).fetchall()

        serialized = []
        for row in items:
            d = dict(row)
            d["is_emergency"] = bool(d["is_emergency"])
            d["checked_in"] = bool(d["checked_in_at"])
            serialized.append(d)

        result.append({
            "department": dict(dept),
            "serving": [x for x in serialized if x["status"] == "serving"],
            "waiting": [x for x in serialized if x["status"] == "waiting"],
        })

    return jsonify({"queues": result}), 200


@admin_bp.route("/stats", methods=["GET"])
@require_auth(["admin", "staff"])
def get_statistics():
    db = get_db()
    recalculate_all_queues(db)

    total_patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    registered_patients = db.execute(
        "SELECT COUNT(*) FROM patients WHERE is_walkin_only = 0"
    ).fetchone()[0]
    total_doctors = db.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
    available_doctors = db.execute(
        "SELECT COUNT(*) FROM doctors WHERE is_available = 1"
    ).fetchone()[0]
    active_departments = db.execute(
        "SELECT COUNT(*) FROM departments WHERE is_active = 1"
    ).fetchone()[0]
    total_appointments = db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]

    status_rows = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM tokens GROUP BY status"
    ).fetchall()
    tokens_by_status = {row["status"]: row["cnt"] for row in status_rows}

    emergency_active = db.execute(
        "SELECT COUNT(*) FROM tokens WHERE is_emergency = 1 AND status IN ('waiting', 'checked_in', 'serving')"
    ).fetchone()[0]

    dept_stats = db.execute(
        """
        SELECT d.id, d.name, d.code,
               COUNT(DISTINCT CASE WHEN q.status = 'waiting' THEN q.id END) AS waiting,
               COUNT(DISTINCT CASE WHEN q.status = 'serving' THEN q.id END) AS serving,
               COUNT(DISTINCT CASE WHEN t.status = 'completed' THEN t.id END) AS completed,
               COUNT(DISTINCT CASE WHEN doc.is_available = 1 THEN doc.id END) AS available_doctors
        FROM departments d
        LEFT JOIN queue q ON q.department_id = d.id
        LEFT JOIN tokens t ON t.department_id = d.id
        LEFT JOIN doctors doc ON doc.department_id = d.id
        WHERE d.is_active = 1
        GROUP BY d.id
        ORDER BY d.id ASC
        """
    ).fetchall()

    return jsonify({
        "summary": {
            "total_patients": total_patients,
            "registered_patients": registered_patients,
            "walkin_patients": total_patients - registered_patients,
            "total_doctors": total_doctors,
            "available_doctors": available_doctors,
            "active_departments": active_departments,
            "total_appointments": total_appointments,
            "total_tokens": sum(tokens_by_status.values()),
            "waiting_tokens": tokens_by_status.get("waiting", 0) + tokens_by_status.get("checked_in", 0),
            "serving_tokens": tokens_by_status.get("serving", 0),
            "completed_tokens": tokens_by_status.get("completed", 0),
            "expired_tokens": tokens_by_status.get("expired", 0),
            "cancelled_tokens": tokens_by_status.get("cancelled", 0),
            "active_emergencies": emergency_active,
        },
        "departments": [dict(r) for r in dept_stats],
    }), 200
