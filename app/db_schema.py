"""Migration-status helpers: is the live database at the revision the code
expects? Used by the WSGI entrypoint (fail fast at boot instead of failing
later with "no such column") and the `flask schema-status` command."""
import os

from flask_migrate import Migrate  # noqa: F401  (ensures the extension is installed)


class SchemaOutOfDateError(RuntimeError):
    """The database schema doesn't match the migrations shipped with this code."""


def _migrations_dir(app):
    cfg = app.extensions["migrate"].directory
    return cfg if os.path.isabs(cfg) else os.path.join(os.path.dirname(app.root_path), cfg)


def schema_status(app):
    """(current_revision_or_None, head_revision) for the app's database."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from app import db

    cfg = Config()
    cfg.set_main_option("script_location", _migrations_dir(app))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    head = heads[0] if len(heads) == 1 else ",".join(sorted(heads))
    with app.app_context():
        with db.engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    return current, head


def verify_schema_current(app):
    """Raise SchemaOutOfDateError (with exact instructions) unless the database
    is at the latest migration. Never modifies the database."""
    try:
        current, head = schema_status(app)
    except Exception as exc:
        raise SchemaOutOfDateError(
            f"Could not read the database's migration state ({type(exc).__name__}: {exc}). "
            "Check DATABASE_URL and that the database is reachable, then run: flask db upgrade"
        ) from exc
    if current != head:
        if current is None:
            how = (
                "This looks like a brand-new database: run `flask db upgrade`.\n"
                "If it is an EXISTING database created by an older version of this app, back it up and run "
                "`flask db stamp 0001_baseline` first, then `flask db upgrade`."
            )
        else:
            how = "Run `flask db upgrade`."
        raise SchemaOutOfDateError(
            f"Database schema is out of date (database at {current or 'no revision'}, code expects {head}).\n{how}"
        )
