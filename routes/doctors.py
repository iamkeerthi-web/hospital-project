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
    validate_email,
    validate_password,
    validate_phone,
)

doctors_bp = Blueprint("doctors", __name__, url_prefix="/api/doctors")


def _serialize_doctor(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "specialization": row["specialization"],
        "department_id": row["department_id"],
        "department_name": row["department_name"],
        "department_code": row["department_code"],
        "phone": row["phone"],
        "email": row["email"],
        "is_available": bool(row["is_available"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fetch_doctor(db, doctor_id: int):
    return db.execute(
        """
        SELECT doc.*, d.name AS department_name, d.code AS department_code
        FROM doctors doc
        JOIN departments d ON d.id = doc.department_id
        WHERE doc.id = ?
        """,
        (doctor_id,),
    ).fetchone()


@doctors_bp.route("", methods=["GET"])
def list_doctors():
    department_id = request.args.get("department_id", type=int)
    available_only = request.args.get("available_only", "false").lower() in ("1", "true", "yes")

    conditions = []
    params = []
    if department_id is not None:
        conditions.append("doc.department_id = ?")
        params.append(department_id)
    if available_only:
        conditions.append("doc.is_available = 1")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    db = get_db()
    rows = db.execute(
        f"""
        SELECT doc.*, d.name AS department_name, d.code AS department_code
        FROM doctors doc
        JOIN departments d ON d.id = doc.department_id
        {where_clause}
        ORDER BY d.id ASC, doc.name ASC
        """,
        params,
    ).fetchall()

    return jsonify({"doctors": [_serialize_doctor(r) for r in rows]}), 200


@doctors_bp.route("/register", methods=["POST"])
def register_doctor():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    name = clean_str(data.get("name"))
    specialization = clean_str(data.get("specialization"))
    email = clean_str(data.get("email")).lower()
    phone = clean_str(data.get("phone")) or None
    password = data.get("password") or ""
    department_id = data.get("department_id")
    is_available = int(bool(data.get("is_available", True)))

    if not name or not specialization:
        return jsonify({"error": "Doctor name and specialization are required"}), 400
    if not email or not validate_email(email):
        return jsonify({"error": "Valid email is required"}), 400
    if phone and not validate_phone(phone):
        return jsonify({"error": "Phone must be a valid 10-digit number"}), 400
    pw_err = validate_password(password)
    if pw_err:
        return jsonify({"error": pw_err}), 400

    try:
        department_id = int(department_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Valid department_id is required"}), 400

    db = get_db()
    dept = db.execute(
        "SELECT id FROM departments WHERE id = ? AND is_active = 1", (department_id,)
    ).fetchone()
    if not dept:
        return jsonify({"error": "Selected department does not exist or is inactive"}), 404

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

    doctor = _fetch_doctor(db, cursor.lastrowid)
    token = create_auth_token(doctor["id"], "doctor", {
        "name": doctor["name"],
        "department_id": doctor["department_id"],
    })
    return jsonify({
        "message": "Doctor registered successfully",
        "auth_token": token,
        "doctor": _serialize_doctor(doctor),
    }), 201


@doctors_bp.route("/login", methods=["POST"])
def login_doctor():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    identifier = clean_str(
        data.get("identifier") or data.get("email") or data.get("phone")
    ).lower()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Email/phone and password are required"}), 400

    db = get_db()
    doctor = db.execute(
        """
        SELECT doc.*, d.name AS department_name, d.code AS department_code
        FROM doctors doc
        JOIN departments d ON d.id = doc.department_id
        WHERE lower(doc.email) = ? OR doc.phone = ?
        """,
        (identifier, identifier),
    ).fetchone()

    if not doctor or not verify_password(password, doctor["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_auth_token(doctor["id"], "doctor", {
        "name": doctor["name"],
        "department_id": doctor["department_id"],
    })
    return jsonify({
        "message": "Doctor login successful",
        "auth_token": token,
        "doctor": _serialize_doctor(doctor),
    }), 200


@doctors_bp.route("/me", methods=["GET"])
@require_auth(["doctor"])
def get_my_doctor_profile():
    db = get_db()
    doctor = _fetch_doctor(db, g.current_user["sub"])
    if not doctor:
        return jsonify({"error": "Doctor not found"}), 404
    return jsonify({"doctor": _serialize_doctor(doctor)}), 200


@doctors_bp.route("/<int:doctor_id>", methods=["GET"])
def get_doctor(doctor_id: int):
    db = get_db()
    doctor = _fetch_doctor(db, doctor_id)
    if not doctor:
        return jsonify({"error": "Doctor not found"}), 404
    return jsonify({"doctor": _serialize_doctor(doctor)}), 200


@doctors_bp.route("/<int:doctor_id>/availability", methods=["PATCH"])
@require_auth(["doctor", "staff", "admin"])
def update_availability(doctor_id: int):
    if g.current_user["role"] == "doctor" and g.current_user["sub"] != doctor_id:
        return jsonify({"error": "Doctors can only update their own availability"}), 403

    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400
    if "is_available" not in data:
        return jsonify({"error": "is_available boolean is required"}), 400

    db = get_db()
    doctor = _fetch_doctor(db, doctor_id)
    if not doctor:
        return jsonify({"error": "Doctor not found"}), 404

    new_state = int(bool(data["is_available"]))
    db.execute(
        "UPDATE doctors SET is_available = ?, updated_at = datetime('now') WHERE id = ?",
        (new_state, doctor_id),
    )
    db.commit()

    updated = _fetch_doctor(db, doctor_id)
    return jsonify({
        "message": "Doctor availability updated",
        "doctor": _serialize_doctor(updated),
    }), 200


@doctors_bp.route("/<int:doctor_id>/department", methods=["PATCH"])
@require_auth(["staff", "admin"])
def assign_department(doctor_id: int):
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    try:
        department_id = int(data.get("department_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valid department_id is required"}), 400

    db = get_db()
    doctor = _fetch_doctor(db, doctor_id)
    if not doctor:
        return jsonify({"error": "Doctor not found"}), 404

    dept = db.execute(
        "SELECT id FROM departments WHERE id = ? AND is_active = 1", (department_id,)
    ).fetchone()
    if not dept:
        return jsonify({"error": "Department not found or inactive"}), 404

    db.execute(
        "UPDATE doctors SET department_id = ?, updated_at = datetime('now') WHERE id = ?",
        (department_id, doctor_id),
    )
    db.commit()

    updated = _fetch_doctor(db, doctor_id)
    return jsonify({
        "message": "Doctor department updated",
        "doctor": _serialize_doctor(updated),
    }), 200


@doctors_bp.route("/<int:doctor_id>", methods=["PUT"])
@require_auth(["doctor", "admin"])
def update_doctor(doctor_id: int):
    if g.current_user["role"] == "doctor" and g.current_user["sub"] != doctor_id:
        return jsonify({"error": "Forbidden"}), 403

    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    db = get_db()
    existing = _fetch_doctor(db, doctor_id)
    if not existing:
        return jsonify({"error": "Doctor not found"}), 404

    name = clean_str(data.get("name")) if "name" in data else existing["name"]
    specialization = (
        clean_str(data.get("specialization"))
        if "specialization" in data
        else existing["specialization"]
    )
    phone = (clean_str(data.get("phone")) or None) if "phone" in data else existing["phone"]
    email = clean_str(data.get("email")).lower() if "email" in data else existing["email"]
    is_available = (
        int(bool(data["is_available"]))
        if "is_available" in data
        else existing["is_available"]
    )

    if not name or not specialization:
        return jsonify({"error": "Name and specialization cannot be empty"}), 400
    if not email or not validate_email(email):
        return jsonify({"error": "Valid email is required"}), 400
    if phone and not validate_phone(phone):
        return jsonify({"error": "Phone must be a valid 10-digit number"}), 400

    department_id = existing["department_id"]
    if "department_id" in data:
        if g.current_user["role"] != "admin":
            return jsonify({"error": "Only admins can reassign departments"}), 403
        try:
            department_id = int(data["department_id"])
        except (TypeError, ValueError):
            return jsonify({"error": "Valid department_id is required"}), 400
        dept = db.execute("SELECT id FROM departments WHERE id = ?", (department_id,)).fetchone()
        if not dept:
            return jsonify({"error": "Department not found"}), 404

    try:
        db.execute(
            """
            UPDATE doctors
            SET name = ?, specialization = ?, department_id = ?, phone = ?,
                email = ?, is_available = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (name, specialization, department_id, phone, email, is_available, doctor_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Email or phone already belongs to another doctor"}), 409

    updated = _fetch_doctor(db, doctor_id)
    return jsonify({
        "message": "Doctor updated",
        "doctor": _serialize_doctor(updated),
    }), 200
