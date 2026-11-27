import sqlite3
from flask import Blueprint, g, jsonify, request

from database.db import get_db
from utils.security import (
    create_auth_token,
    hash_password,
    require_auth,
    verify_password,
)
from utils.validators import (
    clean_str,
    get_json_body,
    validate_age,
    validate_email,
    validate_lang,
    validate_password,
    validate_phone,
)

patients_bp = Blueprint("patients", __name__, url_prefix="/api/patients")


def _serialize_patient(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "age": row["age"],
        "phone": row["phone"],
        "email": row["email"],
        "preferred_lang": row["preferred_lang"],
        "is_walkin_only": bool(row["is_walkin_only"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@patients_bp.route("/register", methods=["POST"])
def register_patient():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    name = clean_str(data.get("name"))
    phone = clean_str(data.get("phone"))
    email = clean_str(data.get("email")).lower() or None
    password = data.get("password") or ""
    preferred_lang = validate_lang(data.get("preferred_lang"))
    age, age_err = validate_age(data.get("age"))

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not validate_phone(phone):
        return jsonify({"error": "Phone must be a valid 10-digit number"}), 400
    if email and not validate_email(email):
        return jsonify({"error": "Invalid email address"}), 400
    if age_err:
        return jsonify({"error": age_err}), 400
    pw_err = validate_password(password)
    if pw_err:
        return jsonify({"error": pw_err}), 400

    db = get_db()
    existing_phone = db.execute(
        "SELECT * FROM patients WHERE phone = ?", (phone,)
    ).fetchone()

    if existing_phone and existing_phone["password_hash"]:
        return jsonify({"error": "A registered patient with this phone already exists"}), 409

    if email:
        existing_email = db.execute(
            "SELECT id FROM patients WHERE email = ?", (email,)
        ).fetchone()
        if existing_email and (not existing_phone or existing_email["id"] != existing_phone["id"]):
            return jsonify({"error": "A patient with this email already exists"}), 409

    pw_hash = hash_password(password)

    try:
        if existing_phone:
            # Upgrade an existing walk-in patient record into a registered account
            db.execute(
                """
                UPDATE patients
                SET name = ?, age = COALESCE(?, age), email = ?, password_hash = ?,
                    preferred_lang = ?, is_walkin_only = 0, updated_at = datetime('now')
                WHERE id = ?
                """,
                (name, age, email, pw_hash, preferred_lang, existing_phone["id"]),
            )
            patient_id = existing_phone["id"]
        else:
            cursor = db.execute(
                """
                INSERT INTO patients (name, age, phone, email, password_hash, preferred_lang, is_walkin_only)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (name, age, phone, email, pw_hash, preferred_lang),
            )
            patient_id = cursor.lastrowid
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Phone or email is already registered"}), 409

    patient = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    token = create_auth_token(patient_id, "patient", {"name": patient["name"]})

    return jsonify({
        "message": "Patient registered successfully",
        "auth_token": token,
        "patient": _serialize_patient(patient),
    }), 201


@patients_bp.route("/login", methods=["POST"])
def login_patient():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    identifier = clean_str(
        data.get("identifier") or data.get("phone") or data.get("email")
    ).lower()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Phone/email and password are required"}), 400

    db = get_db()
    patient = db.execute(
        "SELECT * FROM patients WHERE phone = ? OR lower(email) = ?",
        (identifier, identifier),
    ).fetchone()

    if not patient or not patient["password_hash"] or not verify_password(password, patient["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_auth_token(patient["id"], "patient", {"name": patient["name"]})
    return jsonify({
        "message": "Login successful",
        "auth_token": token,
        "patient": _serialize_patient(patient),
    }), 200


@patients_bp.route("/me", methods=["GET"])
@require_auth(["patient"])
def get_my_profile():
    db = get_db()
    patient = db.execute(
        "SELECT * FROM patients WHERE id = ?", (g.current_user["sub"],)
    ).fetchone()
    if not patient:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify({"patient": _serialize_patient(patient)}), 200


@patients_bp.route("/me", methods=["PUT"])
@require_auth(["patient"])
def update_my_profile():
    return _update_patient_record(g.current_user["sub"])


@patients_bp.route("/me/history", methods=["GET"])
@require_auth(["patient"])
def get_my_history():
    patient_id = g.current_user["sub"]
    db = get_db()

    tokens = db.execute(
        """
        SELECT t.*, d.name AS department_name, d.code AS department_code,
               doc.name AS doctor_name, q.position, q.estimated_wait_mins
        FROM tokens t
        JOIN departments d ON d.id = t.department_id
        LEFT JOIN doctors doc ON doc.id = t.doctor_id
        LEFT JOIN queue q ON q.token_id = t.id
        WHERE t.patient_id = ?
        ORDER BY t.created_at DESC, t.id DESC
        """,
        (patient_id,),
    ).fetchall()

    appointments = db.execute(
        """
        SELECT a.*, d.name AS department_name, d.code AS department_code,
               doc.name AS doctor_name
        FROM appointments a
        JOIN departments d ON d.id = a.department_id
        LEFT JOIN doctors doc ON doc.id = a.doctor_id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_time DESC, a.id DESC
        """,
        (patient_id,),
    ).fetchall()

    return jsonify({
        "tokens": [dict(row) for row in tokens],
        "appointments": [dict(row) for row in appointments],
    }), 200


@patients_bp.route("/<int:patient_id>", methods=["GET"])
@require_auth(["patient", "doctor", "staff", "admin"])
def get_patient(patient_id: int):
    if g.current_user["role"] == "patient" and g.current_user["sub"] != patient_id:
        return jsonify({"error": "Forbidden"}), 403

    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify({"patient": _serialize_patient(patient)}), 200


@patients_bp.route("/<int:patient_id>", methods=["PUT"])
@require_auth(["patient", "staff", "admin"])
def update_patient(patient_id: int):
    if g.current_user["role"] == "patient" and g.current_user["sub"] != patient_id:
        return jsonify({"error": "Forbidden"}), 403
    return _update_patient_record(patient_id)


def _update_patient_record(patient_id: int):
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    db = get_db()
    existing = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Patient not found"}), 404

    name = clean_str(data.get("name")) if "name" in data else existing["name"]
    if not name:
        return jsonify({"error": "Name cannot be empty"}), 400

    if "age" in data:
        age, age_err = validate_age(data.get("age"))
        if age_err:
            return jsonify({"error": age_err}), 400
    else:
        age = existing["age"]

    if "email" in data:
        email = clean_str(data.get("email")).lower() or None
        if email and not validate_email(email):
            return jsonify({"error": "Invalid email address"}), 400
    else:
        email = existing["email"]

    preferred_lang = (
        validate_lang(data.get("preferred_lang"))
        if "preferred_lang" in data
        else existing["preferred_lang"]
    )

    pw_hash = existing["password_hash"]
    if data.get("password"):
        pw_err = validate_password(data["password"])
        if pw_err:
            return jsonify({"error": pw_err}), 400
        pw_hash = hash_password(data["password"])

    try:
        db.execute(
            """
            UPDATE patients
            SET name = ?, age = ?, email = ?, preferred_lang = ?,
                password_hash = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (name, age, email, preferred_lang, pw_hash, patient_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Email is already in use by another patient"}), 409

    updated = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    return jsonify({
        "message": "Patient details updated",
        "patient": _serialize_patient(updated),
    }), 200
