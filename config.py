import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(env_path: Path) -> None:
    """Load key=value pairs from .env without overriding existing environment vars."""
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path, override=False)
        return
    except ImportError:
        pass

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE_DIR / ".env")


class Config:
    """Application configuration driven by environment variables."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-smart-queue-secret-key-change-me")
    AUTH_TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_TOKEN_TTL_SECONDS", "86400"))

    _db_env = os.environ.get("DATABASE_PATH", "smart_queue.db")
    DATABASE_PATH = (
        _db_env
        if os.path.isabs(_db_env)
        else str(BASE_DIR / _db_env)
    )

    TOKEN_EXPIRY_MINS = int(os.environ.get("TOKEN_EXPIRY_MINS", "30"))
    DEFAULT_CONSULT_MINS = int(os.environ.get("DEFAULT_CONSULT_MINS", "7"))
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")

    DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@123")
    DEFAULT_ADMIN_EMAIL = os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@smartqueue.local")
    DEFAULT_STAFF_USERNAME = os.environ.get("DEFAULT_STAFF_USERNAME", "staff")
    DEFAULT_STAFF_PASSWORD = os.environ.get("DEFAULT_STAFF_PASSWORD", "Staff@123")
