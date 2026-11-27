import sqlite3
from flask import Blueprint, current_app, g, jsonify, request

from database.db import get_db
from services.queue_service import (
    cleanup_expired_tokens,
    detect_emergency,
    expiry_str,
    generate_token_number,
    recalculate_department_queue,
)
from utils.security import get_optional_current_user, require_auth
from utils.validators import (
    clean_str,
    get_json_body,
    validate_age,
    validate_lang,
    validate_phone,
)

tokens_bp = Blueprint("tokens", __name__, url_prefix="/api")


def _serialize_token_row(row) -> dict:
    data = dict(row)
    data["is_emergency"] = bool(data.get("is_emergency", 0))
    return data


def _fetch_token_details(db, token_number: str):
    return db.execute(
        """
        SELECT t.*, p.name AS patient_name, p.age AS patient_age, p.phone AS patient_phone,
               d.name AS department_name, d.code AS department_code,
               doc.name AS doctor_name,
               q.position AS queue_position, q.priority_level, q.estimated_wait_mins,
               q.status AS queue_status
        FROM tokens t
        JOIN patients p ON p.id = t.patient_id
        JOIN departments d ON d.id = t.department_id
        LEFT JOIN doctors doc ON doc.id = t.doctor_id
        LEFT JOIN queue q ON q.token_id = t.id
        WHERE t.token_number = ?
        """,
        (token_number,),
    ).fetchone()


@tokens_bp.route("/tokens", methods=["POST"])
def create_token():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    name = clean_str(data.get("name"))
    phone = clean_str(data.get("phone"))
    symptoms = clean_str(data.get("symptoms"))
    translated_symptoms = clean_str(data.get("translated_symptoms")) or symptoms
    spoken_lang = validate_lang(data.get("spoken_lang") or data.get("lang"))
    token_type = clean_str(data.get("token_type") or "walk_in").lower()
    is_emergency_flag = bool(data.get("is_emergency", False))
    age, age_err = validate_age(data.get("age"))

    if token_type not in ("walk_in", "appointment"):
        return jsonify({"error": "token_type must be 'walk_in' or 'appointment'"}), 400
    if age_err:
        return jsonify({"error": age_err}), 400
    if not symptoms:
        return jsonify({"error": "Symptoms are required"}), 400

    current_user = get_optional_current_user()
    db = get_db()

    # Resolve patient: authenticated patient OR walk-in lookup/creation by phone
    patient = None
    if current_user and current_user.get("role") == "patient":
        patient = db.execute(
            "SELECT * FROM patients WHERE id = ?", (current_user["sub"],)
        ).fetchone()
    if patient is None:
        if not name or not validate_phone(phone):
            return jsonify({"error": "Valid name and 10-digit phone number are required"}), 400
        patient = db.execute("SELECT * FROM patients WHERE phone = ?", (phone,)).fetchone()
        if patient is None:
            cursor = db.execute(
                """
                INSERT INTO patients (name, age, phone, preferred_lang, is_walkin_only)
                VALUES (?, ?, ?, ?, 1)
                """,
                (name, age, phone, spoken_lang),
            )
            patient = db.execute(
                "SELECT * FROM patients WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        elif patient["is_walkin_only"]:
            db.execute(
                """
                UPDATE patients
                SET name = ?, age = COALESCE(?, age), preferred_lang = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (name, age, spoken_lang, patient["id"]),
            )

    # Detect emergency automatically from checkbox or multilingual keywords
    is_emergency = is_emergency_flag or detect_emergency(f"{symptoms} {translated_symptoms}")
    priority_level = int(data.get("priority_level", 10 if is_emergency else 0))
    if is_emergency and priority_level < 10:
        priority_level = 10

    # Resolve department: explicit ID or default (EMG for emergency, GEN otherwise)
    department_id = data.get("department_id")
    if department_id is not None and str(department_id).strip() != "":
        try:
            department_id = int(department_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid department_id"}), 400
        dept = db.execute(
            "SELECT * FROM departments WHERE id = ? AND is_active = 1", (department_id,)
        ).fetchone()
    else:
        default_code = "EMG" if is_emergency else "GEN"
        dept = db.execute(
            "SELECT * FROM departments WHERE code = ? AND is_active = 1", (default_code,)
        ).fetchone()
        if not dept:
            dept = db.execute(
                "SELECT * FROM departments WHERE is_active = 1 ORDER BY id ASC LIMIT 1"
            ).fetchone()

    if not dept:
        return jsonify({"error": "Selected department not found or inactive"}), 404

    doctor_id = data.get("doctor_id")
    if doctor_id is not None and str(doctor_id).strip() != "":
        try:
            doctor_id = int(doctor_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid doctor_id"}), 400
        doc = db.execute(
            "SELECT id, department_id FROM doctors WHERE id = ?", (doctor_id,)
        ).fetchone()
        if not doc:
            return jsonify({"error": "Selected doctor not found"}), 404
        if doc["department_id"] != dept["id"]:
            return jsonify({"error": "Selected doctor does not belong to the chosen department"}), 400
    else:
        doc = db.execute(
            "SELECT id FROM doctors WHERE department_id = ? AND is_available = 1 ORDER BY id ASC LIMIT 1",
            (dept["id"],),
        ).fetchone()
        doctor_id = doc["id"] if doc else None

    appointment_id = data.get("appointment_id")
    if appointment_id is not None:
        try:
            appointment_id = int(appointment_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid appointment_id"}), 400

    token_number = generate_token_number(db, dept["id"], dept["code"], is_emergency)
    expiry_mins = current_app.config.get("TOKEN_EXPIRY_MINS", 30)
    expires_at = expiry_str(expiry_mins)

    try:
        cursor = db.execute(
            """
            INSERT INTO tokens (
                token_number, patient_id, department_id, doctor_id, appointment_id,
                token_type, symptoms, translated_symptoms, spoken_lang,
                is_emergency, priority_score, status, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?)
            """,
            (
                token_number,
                patient["id"],
                dept["id"],
                doctor_id,
                appointment_id,
                token_type,
                symptoms,
                translated_symptoms,
                spoken_lang,
                int(is_emergency),
                priority_level,
                expires_at,
            ),
        )
        token_id = cursor.lastrowid

        db.execute(
            """
            INSERT INTO queue (department_id, token_id, doctor_id, position, priority_level, status)
            VALUES (?, ?, ?, 0, ?, 'waiting')
            """,
            (dept["id"], token_id, doctor_id, priority_level),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Failed to generate token, please retry"}), 409

    recalculate_department_queue(db, dept["id"])
    token_row = _fetch_token_details(db, token_number)

    return jsonify({
        "message": "Token issued successfully",
        "token": _serialize_token_row(token_row),
    }), 201


@tokens_bp.route("/tokens/checkin", methods=["POST"])
def checkin_token():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    phone = clean_str(data.get("phone"))
    token_number = clean_str(data.get("token_number")).upper()

    if not phone and not token_number:
        return jsonify({"error": "Provide a 10-digit phone number or token_number"}), 400
    if phone and not validate_phone(phone):
        return jsonify({"error": "Enter a valid 10-digit phone number"}), 400

    db = get_db()
    cleanup_expired_tokens(db)

    if token_number:
        row = db.execute(
            """
            SELECT t.*, p.name AS patient_name, p.phone AS patient_phone
            FROM tokens t
            JOIN patients p ON p.id = t.patient_id
            WHERE upper(t.token_number) = ?
              AND t.status = 'waiting'
              AND t.checked_in_at IS NULL
            """,
            (token_number,),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT t.*, p.name AS patient_name, p.phone AS patient_phone
            FROM tokens t
            JOIN patients p ON p.id = t.patient_id
            WHERE p.phone = ?
              AND t.status = 'waiting'
              AND t.checked_in_at IS NULL
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT 1
            """,
            (phone,),
        ).fetchone()

    if not row:
        return jsonify({
            "error": "No active unchecked-in token found. It may be expired or already checked in."
        }), 404

    db.execute(
        """
        UPDATE tokens
        SET status = 'checked_in', checked_in_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
        """,
        (row["id"],),
    )
    if row["appointment_id"]:
        db.execute(
            "UPDATE appointments SET status = 'checked_in', updated_at = datetime('now') WHERE id = ?",
            (row["appointment_id"],),
        )
    db.commit()

    updated = _fetch_token_details(db, row["token_number"])
    return jsonify({
        "message": f"{row['patient_name']} checked in. Token {row['token_number']} secured.",
        "token": _serialize_token_row(updated),
    }), 200


@tokens_bp.route("/tokens/<string:token_number>", methods=["GET"])
def track_token(token_number: str):
    db = get_db()
    cleanup_expired_tokens(db)
    row = _fetch_token_details(db, clean_str(token_number).upper())
    if not row:
        return jsonify({"error": "Token not found"}), 404
    return jsonify({"token": _serialize_token_row(row)}), 200


@tokens_bp.route("/tokens/<string:token_number>/cancel", methods=["PATCH"])
@require_auth(["patient", "staff", "admin"])
def cancel_token(token_number: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tokens WHERE upper(token_number) = ?",
        (clean_str(token_number).upper(),),
    ).fetchone()
    if not row:
        return jsonify({"error": "Token not found"}), 404

    if g.current_user["role"] == "patient" and g.current_user["sub"] != row["patient_id"]:
        return jsonify({"error": "You can only cancel your own token"}), 403
    if row["status"] in ("completed", "cancelled", "expired"):
        return jsonify({"error": f"Token is already {row['status']}"}), 400

    db.execute(
        "UPDATE tokens SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
        (row["id"],),
    )
    db.execute(
        "UPDATE queue SET status = 'skipped', position = 0, estimated_wait_mins = 0, updated_at = datetime('now') WHERE token_id = ?",
        (row["id"],),
    )
    db.commit()
    recalculate_department_queue(db, row["department_id"])

    return jsonify({"message": f"Token {row['token_number']} cancelled"}), 200


@tokens_bp.route("/tokens/cleanup-expired", methods=["POST"])
def trigger_cleanup():
    db = get_db()
    removed = cleanup_expired_tokens(db)
    return jsonify({"expired_count": removed}), 200


# ---------------- Appointments ----------------

@tokens_bp.route("/appointments", methods=["POST"])
@require_auth(["patient", "staff", "admin"])
def create_appointment():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    symptoms = clean_str(data.get("symptoms"))
    translated_symptoms = clean_str(data.get("translated_symptoms")) or symptoms
    spoken_lang = validate_lang(data.get("spoken_lang") or data.get("lang"))
    appointment_time = clean_str(data.get("appointment_time"))

    if not symptoms or not appointment_time:
        return jsonify({"error": "appointment_time and symptoms are required"}), 400

    try:
        department_id = int(data.get("department_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valid department_id is required"}), 400

    db = get_db()
    if g.current_user["role"] == "patient":
        patient_id = g.current_user["sub"]
    else:
        try:
            patient_id = int(data.get("patient_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Valid patient_id is required"}), 400

    patient = db.execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        return jsonify({"error": "Patient not found"}), 404

    dept = db.execute(
        "SELECT id FROM departments WHERE id = ? AND is_active = 1", (department_id,)
    ).fetchone()
    if not dept:
        return jsonify({"error": "Department not found or inactive"}), 404

    doctor_id = data.get("doctor_id")
    if doctor_id is not None and str(doctor_id).strip() != "":
        try:
            doctor_id = int(doctor_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid doctor_id"}), 400
        doc = db.execute("SELECT id, department_id FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if not doc or doc["department_id"] != department_id:
            return jsonify({"error": "Invalid doctor for the selected department"}), 400
    else:
        doctor_id = None

    cursor = db.execute(
        """
        INSERT INTO appointments (
            patient_id, department_id, doctor_id, appointment_time,
            symptoms, translated_symptoms, spoken_lang, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled')
        """,
        (patient_id, department_id, doctor_id, appointment_time, symptoms, translated_symptoms, spoken_lang),
    )
    db.commit()

    appt = db.execute(
        """
        SELECT a.*, p.name AS patient_name, d.name AS department_name, doc.name AS doctor_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN departments d ON d.id = a.department_id
        LEFT JOIN doctors doc ON doc.id = a.doctor_id
        WHERE a.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    return jsonify({
        "message": "Appointment booked successfully",
        "appointment": dict(appt),
    }), 201


@tokens_bp.route("/appointments", methods=["GET"])
@require_auth(["patient", "doctor", "staff", "admin"])
def list_appointments():
    db = get_db()
    conditions = []
    params = []

    if g.current_user["role"] == "patient":
        conditions.append("a.patient_id = ?")
        params.append(g.current_user["sub"])
    else:
        dept_id = request.args.get("department_id", type=int)
        if dept_id is not None:
            conditions.append("a.department_id = ?")
            params.append(dept_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = db.execute(
        f"""
        SELECT a.*, p.name AS patient_name, p.phone AS patient_phone,
               d.name AS department_name, d.code AS department_code,
               doc.name AS doctor_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN departments d ON d.id = a.department_id
        LEFT JOIN doctors doc ON doc.id = a.doctor_id
        {where_clause}
        ORDER BY a.appointment_time ASC, a.id DESC
        """,
        params,
    ).fetchall()

    return jsonify({"appointments": [dict(r) for r in rows]}), 200


@tokens_bp.route("/appointments/<int:appt_id>/checkin", methods=["POST"])
@require_auth(["patient", "staff", "admin"])
def checkin_appointment(appt_id: int):
    db = get_db()
    appt = db.execute(
        """
        SELECT a.*, d.code AS department_code
        FROM appointments a
        JOIN departments d ON d.id = a.department_id
        WHERE a.id = ?
        """,
        (appt_id,),
    ).fetchone()
    if not appt:
        return jsonify({"error": "Appointment not found"}), 404
    if g.current_user["role"] == "patient" and g.current_user["sub"] != appt["patient_id"]:
        return jsonify({"error": "Forbidden"}), 403
    if appt["status"] != "scheduled":
        return jsonify({"error": f"Appointment is already {appt['status']}"}), 400

    existing_token = db.execute(
        "SELECT token_number FROM tokens WHERE appointment_id = ? AND status NOT IN ('cancelled', 'expired')",
        (appt_id,),
    ).fetchone()
    if existing_token:
        return jsonify({"error": f"Token {existing_token['token_number']} already issued for this appointment"}), 409

    is_emergency = detect_emergency(f"{appt['symptoms']} {appt['translated_symptoms']}")
    priority_level = 10 if is_emergency else 2  # Slight priority boost for booked appointments
    token_number = generate_token_number(db, appt["department_id"], appt["department_code"], is_emergency)
    expires_at = expiry_str(current_app.config.get("TOKEN_EXPIRY_MINS", 30))

    cursor = db.execute(
        """
        INSERT INTO tokens (
            token_number, patient_id, department_id, doctor_id, appointment_id,
            token_type, symptoms, translated_symptoms, spoken_lang,
            is_emergency, priority_score, status, checked_in_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, 'appointment', ?, ?, ?, ?, ?, 'checked_in', datetime('now'), ?)
        """,
        (
            token_number,
            appt["patient_id"],
            appt["department_id"],
            appt["doctor_id"],
            appt["id"],
            appt["symptoms"],
            appt["translated_symptoms"],
            appt["spoken_lang"],
            int(is_emergency),
            priority_level,
            expires_at,
        ),
    )
    token_id = cursor.lastrowid

    db.execute(
        """
        INSERT INTO queue (department_id, token_id, doctor_id, position, priority_level, status)
        VALUES (?, ?, ?, 0, ?, 'waiting')
        """,
        (appt["department_id"], token_id, appt["doctor_id"], priority_level),
    )
    db.execute(
        "UPDATE appointments SET status = 'checked_in', updated_at = datetime('now') WHERE id = ?",
        (appt_id,),
    )
    db.commit()

    recalculate_department_queue(db, appt["department_id"])
    token_row = _fetch_token_details(db, token_number)

    return jsonify({
        "message": "Appointment checked in and token generated",
        "token": _serialize_token_row(token_row),
    }), 201
