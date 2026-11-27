from functools import wraps
from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password: str) -> str:
    """Hash a plaintext password using Werkzeug's salted PBKDF2/scrypt hash."""
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    if not password or not password_hash:
        return False
    return check_password_hash(password_hash, password)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="sqms-auth-v1")


def create_auth_token(user_id: int, role: str, extra: dict | None = None) -> str:
    """Generate a signed, expiring authentication token."""
    payload = {"sub": user_id, "role": role}
    if extra:
        payload.update(extra)
    return _serializer().dumps(payload)


def decode_auth_token(token: str) -> dict | None:
    """Decode and validate an auth token; returns None if invalid or expired."""
    max_age = current_app.config.get("AUTH_TOKEN_TTL_SECONDS", 86400)
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def extract_bearer_token() -> str | None:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def get_optional_current_user() -> dict | None:
    """Return decoded user payload if valid Bearer token is present, else None."""
    token = extract_bearer_token()
    if not token:
        return None
    return decode_auth_token(token)


def require_auth(roles: tuple[str, ...] | list[str] | None = None):
    """Protect an endpoint with Bearer token authentication and optional role check."""
    allowed_roles = set(roles) if roles else None

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = extract_bearer_token()
            if not token:
                return jsonify({"error": "Authentication required"}), 401
            payload = decode_auth_token(token)
            if not payload:
                return jsonify({"error": "Invalid or expired authentication token"}), 401
            if allowed_roles and payload.get("role") not in allowed_roles:
                return jsonify({"error": "Insufficient permissions for this action"}), 403
            g.current_user = payload
            return fn(*args, **kwargs)

        return wrapper

    return decorator
