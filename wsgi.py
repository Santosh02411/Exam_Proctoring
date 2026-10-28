"""Production WSGI entry point — used by Gunicorn/uWSGI instead of run.py's
Flask dev server. Example: gunicorn --bind 0.0.0.0:8000 wsgi:app

Startup is deliberately strict:
  * create_app() refuses to start on missing/placeholder secrets or invalid
    environment variables (app.config_validation);
  * this module refuses to serve if the database isn't at the latest
    migration — apply them with `flask db upgrade` first. (Skipped for
    `flask ...` CLI commands, which is how `flask db upgrade` itself runs.)
"""
import os

from app import create_app
from app.db_schema import SchemaOutOfDateError, verify_schema_current

app = create_app()

if not os.environ.get("FLASK_RUN_FROM_CLI") and not os.environ.get("SKIP_SCHEMA_CHECK"):
    try:
        verify_schema_current(app)
    except SchemaOutOfDateError as exc:
        app.logger.critical("Refusing to start: %s", exc)
        raise
