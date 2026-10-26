"""Schema is owned by Alembic migrations — not by the app at startup."""
import os
import shutil
import sqlite3

import pytest

from tests.conftest import TestConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(ROOT, "migrations")


def _app_for(db_path):
    from app import create_app

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    return create_app(config_object=Cfg())


def _tables(db_path):
    con = sqlite3.connect(db_path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_creating_the_app_does_not_touch_the_schema(tmp_path):
    """The old create_app() ran create_all() + a column patcher that swallowed
    errors. Booting must now leave the database exactly as it found it."""
    db_path = str(tmp_path / "fresh.db")
    _app_for(db_path)
    assert _tables(db_path) == set()


def test_upgrade_builds_a_schema_identical_to_the_models(tmp_path):
    """Migrations and models must not drift: after `upgrade` to head, Alembic's
    autogenerate comparison finds nothing left to do."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from flask_migrate import upgrade
    from app import db

    db_path = str(tmp_path / "up.db")
    app = _app_for(db_path)
    with app.app_context():
        upgrade(directory=MIGRATIONS)
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), db.metadata)
    assert diff == [], f"models and migrations have drifted — generate a new revision: {diff}"


def test_downgrade_to_base_then_upgrade_again(tmp_path):
    from flask_migrate import downgrade, upgrade

    app = _app_for(str(tmp_path / "round.db"))
    with app.app_context():
        upgrade(directory=MIGRATIONS)
        downgrade(directory=MIGRATIONS, revision="base")
        upgrade(directory=MIGRATIONS)


def test_unmigrated_database_is_reported_clearly(tmp_path):
    from app.db_schema import SchemaOutOfDateError, verify_schema_current

    app = _app_for(str(tmp_path / "empty.db"))
    with pytest.raises(SchemaOutOfDateError) as exc:
        verify_schema_current(app)
    msg = str(exc.value)
    assert "flask db upgrade" in msg and "stamp 0001_baseline" in msg


def test_up_to_date_database_passes_the_check(tmp_path):
    from flask_migrate import upgrade
    from app.db_schema import verify_schema_current

    app = _app_for(str(tmp_path / "ok.db"))
    with app.app_context():
        upgrade(directory=MIGRATIONS)
    verify_schema_current(app)  # no exception


def test_a_database_one_revision_behind_is_flagged_and_then_upgradable(tmp_path):
    from flask_migrate import upgrade
    from app.db_schema import SchemaOutOfDateError, verify_schema_current

    app = _app_for(str(tmp_path / "behind.db"))
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="0001_baseline")
    with pytest.raises(SchemaOutOfDateError) as exc:
        verify_schema_current(app)
    assert "0001_baseline" in str(exc.value)
    with app.app_context():
        upgrade(directory=MIGRATIONS)
    verify_schema_current(app)


def test_existing_pre_migration_database_can_be_adopted_by_stamping(tmp_path):
    """The documented path for a database created by the old create_all():
    `flask db stamp 0001_baseline`, then `flask db upgrade`."""
    from flask_migrate import stamp, upgrade
    from app import db
    from app.db_schema import verify_schema_current

    app = _app_for(str(tmp_path / "legacy.db"))
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="0001_baseline")   # stands in for the legacy schema
        with db.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM alembic_version")   # legacy DBs have no version table
        stamp(directory=MIGRATIONS, revision="0001_baseline")
        upgrade(directory=MIGRATIONS)
    verify_schema_current(app)


def test_a_failing_migration_is_reported_clearly_and_not_applied(tmp_path, capfd):
    """A broken revision must raise (so `flask db upgrade` exits non-zero and
    the container refuses to start), say so in the log, and leave the database
    at the previous revision rather than half-migrated."""
    from flask_migrate import upgrade
    from app.db_schema import schema_status

    broken = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, broken, ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "versions" / "0003_broken.py").write_text(
        '"""broken"""\nrevision = "0003_broken"\ndown_revision = "0002_invites_leases"\n'
        "branch_labels = None\ndepends_on = None\n"
        "from alembic import op\nimport sqlalchemy as sa\n"
        "def upgrade():\n"
        "    op.add_column('users', sa.Column('half_done', sa.Integer()))\n"
        "    raise RuntimeError('boom — simulated failure')\n"
        "def downgrade():\n    pass\n"
    )
    app = _app_for(str(tmp_path / "fail.db"))
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="0002_invites_leases")
        # Flask-Migrate turns a failed migration into SystemExit(1), which is
        # exactly what makes `flask db upgrade` exit non-zero.
        with pytest.raises(SystemExit):
            upgrade(directory=str(broken))
    assert "MIGRATION FAILED" in capfd.readouterr().err
    current, _head = schema_status(app)
    assert current == "0002_invites_leases"
    # (On PostgreSQL the failed revision's DDL is rolled back entirely; SQLite DDL
    # is non-transactional, so the revision pointer staying put is the guarantee.)


def test_cli_schema_status_exits_nonzero_when_behind(tmp_path):
    from flask_migrate import upgrade

    app = _app_for(str(tmp_path / "cli.db"))
    runner = app.test_cli_runner()
    assert runner.invoke(args=["schema-status"]).exit_code != 0
    with app.app_context():
        upgrade(directory=MIGRATIONS)
    result = runner.invoke(args=["schema-status"])
    assert result.exit_code == 0 and "up to date" in result.output


def test_init_db_command_is_gone_and_points_at_migrations(tmp_path):
    app = _app_for(str(tmp_path / "x.db"))
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code != 0 and "flask db upgrade" in result.output
