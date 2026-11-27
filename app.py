import os
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from config import Config
from database.db import close_db, init_db
from database.seed import seed_data

BASE_DIR = Path(__file__).resolve().parent


def create_app(config_overrides: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    CORS(
        app,
        resources={r"/api/*": {"origins": app.config.get("CORS_ORIGINS", "*")}},
        supports_credentials=False,
    )

    # Ensure database directory exists and initialize schema + seed data
    db_path = app.config["DATABASE_PATH"]
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    init_db(db_path)
    seed_data(db_path, app.config)

    app.teardown_appcontext(close_db)

    @app.route("/", methods=["GET"])
    @app.route("/finalcode.html", methods=["GET"])
    def serve_frontend():
        return send_from_directory(str(BASE_DIR), "finalcode.html")

    @app.route("/tt.html", methods=["GET"])
    def serve_legacy_frontend():
        return send_from_directory(str(BASE_DIR), "tt.html")

    @app.route("/api/health", methods=["GET"])
    def health_check():
        return jsonify({
            "status": "ok",
            "service": "Smart Queue Management System API",
        }), 200

    # Register blueprints as modules are added
    _register_blueprints(app)

    @app.errorhandler(404)
    def handle_404(_err):
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(405)
    def handle_405(_err):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def handle_500(_err):
        return jsonify({"error": "Internal server error"}), 500

    return app


def _register_blueprints(app: Flask) -> None:
    """Import and register route blueprints if implemented."""
    blueprint_specs = [
        ("routes.patients", "patients_bp"),
        ("routes.departments", "departments_bp"),
        ("routes.doctors", "doctors_bp"),
        ("routes.tokens", "tokens_bp"),
        ("routes.queue", "queue_bp"),
        ("routes.admin", "admin_bp"),
    ]
    for module_name, attr in blueprint_specs:
        try:
            mod = __import__(module_name, fromlist=[attr])
            bp = getattr(mod, attr, None)
            if bp is not None:
                app.register_blueprint(bp)
        except ImportError:
            pass


if __name__ == "__main__":
    application = create_app()
    application.run(
        host=application.config["HOST"],
        port=application.config["PORT"],
        debug=application.config["DEBUG"],
    )
