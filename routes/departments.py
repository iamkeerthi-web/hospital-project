import sqlite3
from flask import Blueprint, jsonify, request

from database.db import get_db
from utils.security import require_auth
from utils.validators import clean_str, get_json_body

departments_bp = Blueprint("departments", __name__, url_prefix="/api/departments")


@departments_bp.route("", methods=["GET"])
def list_departments():
    active_only = request.args.get("active_only", "true").lower() in ("1", "true", "yes")
    db = get_db()

    where_clause = "WHERE d.is_active = 1" if active_only else ""
    rows = db.execute(
        f"""
        SELECT d.*,
               COUNT(DISTINCT CASE WHEN doc.is_available = 1 THEN doc.id END) AS available_doctors,
               COUNT(DISTINCT doc.id) AS total_doctors,
               COUNT(DISTINCT CASE WHEN q.status = 'waiting' THEN q.id END) AS waiting_count
        FROM departments d
        LEFT JOIN doctors doc ON doc.department_id = d.id
        LEFT JOIN queue q ON q.department_id = d.id AND q.status = 'waiting'
        {where_clause}
        GROUP BY d.id
        ORDER BY d.id ASC
        """
    ).fetchall()

    return jsonify({"departments": [dict(r) for r in rows]}), 200


@departments_bp.route("/<int:dept_id>", methods=["GET"])
def get_department(dept_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not row:
        return jsonify({"error": "Department not found"}), 404
    return jsonify({"department": dict(row)}), 200


@departments_bp.route("", methods=["POST"])
@require_auth(["admin"])
def create_department():
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    name = clean_str(data.get("name"))
    code = clean_str(data.get("code")).upper()
    description = clean_str(data.get("description"))

    try:
        avg_consult_mins = int(data.get("avg_consult_mins", 7))
    except (TypeError, ValueError):
        return jsonify({"error": "avg_consult_mins must be a positive integer"}), 400

    if not name or not code:
        return jsonify({"error": "Department name and code are required"}), 400
    if len(code) > 10 or not code.isalnum():
        return jsonify({"error": "Department code must be 1-10 alphanumeric characters"}), 400
    if avg_consult_mins < 1 or avg_consult_mins > 120:
        return jsonify({"error": "avg_consult_mins must be between 1 and 120"}), 400

    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO departments (name, code, description, avg_consult_mins, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (name, code, description, avg_consult_mins),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Department name or code already exists"}), 409

    dept = db.execute("SELECT * FROM departments WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"message": "Department created", "department": dict(dept)}), 201


@departments_bp.route("/<int:dept_id>", methods=["PUT"])
@require_auth(["admin"])
def update_department(dept_id: int):
    data, err = get_json_body()
    if err:
        return jsonify({"error": err}), 400

    db = get_db()
    existing = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Department not found"}), 404

    name = clean_str(data.get("name")) if "name" in data else existing["name"]
    code = clean_str(data.get("code")).upper() if "code" in data else existing["code"]
    description = clean_str(data.get("description")) if "description" in data else existing["description"]
    is_active = int(bool(data["is_active"])) if "is_active" in data else existing["is_active"]

    if "avg_consult_mins" in data:
        try:
            avg_consult_mins = int(data["avg_consult_mins"])
        except (TypeError, ValueError):
            return jsonify({"error": "avg_consult_mins must be a positive integer"}), 400
        if avg_consult_mins < 1 or avg_consult_mins > 120:
            return jsonify({"error": "avg_consult_mins must be between 1 and 120"}), 400
    else:
        avg_consult_mins = existing["avg_consult_mins"]

    if not name or not code:
        return jsonify({"error": "Department name and code cannot be empty"}), 400

    try:
        db.execute(
            """
            UPDATE departments
            SET name = ?, code = ?, description = ?, avg_consult_mins = ?, is_active = ?
            WHERE id = ?
            """,
            (name, code, description, avg_consult_mins, is_active, dept_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Department name or code already exists"}), 409

    dept = db.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
    return jsonify({"message": "Department updated", "department": dict(dept)}), 200
