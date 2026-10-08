"""_ensure_columns (app/__init__.py) reconciles every model's columns
against whatever a live, previously-deployed database actually has, and
ALTERs in anything missing. This replaced an earlier version that only knew
about a short, hand-maintained list of two columns — which a real
deployment outgrew immediately: several `users` columns (2FA, SSO,
notification preferences) predated that list and were never added to it,
so `flask seed-admin` crashed with "no such column: users.totp_secret" the
moment it ran against an existing database. These tests simulate that exact
class of database (several real, unrelated columns missing across more than
one table) rather than only the two columns a previous version happened to
track, so this can't silently regress back to a hand-maintained list.
"""
import sqlite3

from tests.conftest import TestConfig


def _drop_columns(db_path, table, columns_to_drop):
    """Simulate an old database missing `columns_to_drop` from `table`,
    while preserving everything else about its real schema (PRIMARY KEY,
    AUTOINCREMENT, etc.) exactly as a genuine previously-deployed database
    would have it. `CREATE TABLE ... AS SELECT` is tempting here but wrong:
    it silently drops PRIMARY KEY/AUTOINCREMENT from the copy, which would
    make a real ORM insert behave strangely for reasons that have nothing
    to do with the migration logic under test — a real deployed database's
    id column was always a proper primary key."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    create_sql = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    cur.execute(f"PRAGMA table_info({table})")
    keep = [r[1] for r in cur.fetchall() if r[1] not in columns_to_drop]

    lines = [ln.strip().rstrip(",") for ln in create_sql.split("\n")]
    header, body_lines = lines[0], lines[1:-1]
    kept_lines = [ln for ln in body_lines if not any(ln.startswith(f'"{c}"') or ln.startswith(c) for c in columns_to_drop)]
    new_sql = header + "\n  " + ",\n  ".join(kept_lines) + "\n)"

    cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    cur.execute(new_sql.replace(f'"{table}"', table, 1).replace(f"TABLE {table}", f"TABLE {table}", 1))
    cur.execute(f"INSERT INTO {table} ({', '.join(keep)}) SELECT {', '.join(keep)} FROM {table}_old")
    cur.execute(f"DROP TABLE {table}_old")
    con.commit()
    con.close()


def _columns_of(db_path, table):
    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    con.close()
    return cols


def test_a_database_that_predates_2fa_sso_and_notification_prefs_boots_and_queries_clean(tmp_path):
    """The exact real-world case: a users table missing several columns
    that were added across unrelated features, long before max_warnings/
    total_warning_count existed — not a scenario the old 2-column list
    ever covered."""
    from app import create_app

    db_path = str(tmp_path / "pre_2fa.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    missing = ["totp_secret", "totp_confirmed", "backup_codes", "sso_provider", "sso_subject", "notification_prefs"]
    _drop_columns(db_path, "users", missing)
    assert all(c not in _columns_of(db_path, "users") for c in missing)

    app2 = create_app(config_object=OldConfig())  # this is the moment that used to crash
    with app2.app_context():
        from app.models import User
        User.query.count()  # would raise "no such column" before the fix
        u = User(name="A", email="a@example.com", password_hash="x", role="student", org_id=None)
        from app import db
        db.session.add(u)
        db.session.commit()
        fetched = User.query.filter_by(email="a@example.com").first()
        assert fetched is not None
        assert fetched.totp_confirmed is False  # the model's own default, now actually enforceable
        assert fetched.notification_prefs is None


def test_seed_admin_cli_command_works_against_a_database_missing_those_columns(tmp_path):
    """The literal command and database shape from the real bug report:
    `flask seed-admin` against a database missing users.totp_secret."""
    from app import create_app, db

    db_path = str(tmp_path / "seed_admin_old.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    _drop_columns(db_path, "users", ["totp_secret", "totp_confirmed", "backup_codes", "sso_provider", "sso_subject", "notification_prefs"])

    app2 = create_app(config_object=OldConfig())
    result = app2.test_cli_runner().invoke(args=["seed-admin", "--email", "admin@example.com", "--password", "Testpass1!"])
    assert result.exit_code == 0, result.output
    assert "Admin created" in result.output

    con = sqlite3.connect(db_path)
    row = con.execute("select email, role from users where email='admin@example.com'").fetchone()
    con.close()
    assert row == ("admin@example.com", "admin")


def test_multiple_tables_missing_different_columns_are_all_fixed_in_one_boot(tmp_path):
    """Not just one table at a time -- a real multi-feature-old database
    is missing different things in different tables simultaneously."""
    from app import create_app

    db_path = str(tmp_path / "multi.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    _drop_columns(db_path, "users", ["totp_secret", "backup_codes"])
    _drop_columns(db_path, "tests", ["max_warnings"])
    _drop_columns(db_path, "attempts", ["total_warning_count"])

    app2 = create_app(config_object=OldConfig())
    with app2.app_context():
        from app.models import User, Test, Attempt
        User.query.count()
        Test.query.count()
        Attempt.query.count()
    for table, col in (("users", "totp_secret"), ("users", "backup_codes"), ("tests", "max_warnings"), ("attempts", "total_warning_count")):
        assert col in _columns_of(db_path, table)


def test_a_not_null_literal_default_column_is_restored_with_its_constraint_intact(tmp_path):
    """attempts.total_warning_count is NOT NULL DEFAULT 0 on the model --
    the reconciled column should enforce that too, not just exist."""
    from app import create_app

    db_path = str(tmp_path / "notnull.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    _drop_columns(db_path, "attempts", ["total_warning_count"])

    create_app(config_object=OldConfig())  # triggers reconciliation
    con = sqlite3.connect(db_path)
    info = {r[1]: r for r in con.execute("PRAGMA table_info(attempts)")}
    con.close()
    col = info["total_warning_count"]
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    assert col[3] == 1, f"expected NOT NULL, got notnull={col[3]}"
    assert col[4] == "0", f"expected default 0, got {col[4]!r}"


def test_a_callable_default_column_is_added_nullable_rather_than_crashing(tmp_path):
    """users.created_at defaults to datetime.utcnow -- a callable can't be
    expressed as a static SQL DEFAULT, so reconciliation must fall back to
    adding the column as nullable instead of generating invalid DDL or
    refusing to add it at all."""
    from app import create_app

    db_path = str(tmp_path / "callable_default.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    _drop_columns(db_path, "users", ["created_at"])

    app2 = create_app(config_object=OldConfig())  # must not raise
    with app2.app_context():
        from app.models import User
        User.query.count()
    assert "created_at" in _columns_of(db_path, "users")


def test_an_already_current_database_is_untouched_and_boots_clean(tmp_path):
    """The common case: a database that already has every column. Running
    reconciliation against it must be a safe no-op, not an error."""
    from app import create_app

    db_path = str(tmp_path / "current.db")

    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    create_app(config_object=OldConfig())
    before = _columns_of(db_path, "users")

    create_app(config_object=OldConfig())  # second boot: everything already present
    after = _columns_of(db_path, "users")
    assert before == after
