import re

PHONE_RE = re.compile(r"^\d{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SUPPORTED_LANGS = {"en", "hi", "kn", "ta", "te"}


def get_json_body() -> tuple[dict | None, str | None]:
    """Safely parse JSON request body and return (data, error_message)."""
    data = request_json()
    if data is None or not isinstance(data, dict):
        return None, "Request body must be valid JSON object"
    return data, None


def request_json():
    from flask import request
    return request.get_json(silent=True)


def clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def validate_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(clean_str(phone)))


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(clean_str(email)))


def validate_age(age) -> tuple[int | None, str | None]:
    if age is None or str(age).strip() == "":
        return None, None
    try:
        val = int(age)
    except (TypeError, ValueError):
        return None, "Age must be a valid whole number"
    if val < 0 or val > 120:
        return None, "Age must be between 0 and 120"
    return val, None


def validate_lang(lang: str | None) -> str:
    cleaned = clean_str(lang).lower()
    return cleaned if cleaned in SUPPORTED_LANGS else "en"


def validate_password(password: str) -> str | None:
    if not password or len(password) < 6:
        return "Password must be at least 6 characters long"
    return None
