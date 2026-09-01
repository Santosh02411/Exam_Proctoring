"""Automated backups. This app has no background scheduler of its own (the
same "bring your own cron" pattern as send-reminders and retention cleanup)
— `flask backup-db` is meant to be run periodically by an external
cron/Task Scheduler entry, and /ops/backups exposes the same action as a
manual button plus a list of what's already been taken.

Three engines are supported, dispatched from SQLALCHEMY_DATABASE_URI:

- **sqlite** (this app's default): a straight file copy, since the
  database *is* a single file.
- **postgresql**: shells out to `pg_dump` (custom format, restorable with
  `pg_restore`), if it's installed and on PATH.
- **mysql**: shells out to `mysqldump` (plain SQL), if it's installed and
  on PATH.

Any other engine, or a missing pg_dump/mysqldump binary, raises a clear
ValueError explaining exactly what's missing and what to do instead
(install the client tools, or use the database provider's own backup
tooling/managed snapshots) — never silently no-ops.
"""

import os
import re
import shutil
import subprocess
from datetime import datetime
from urllib.parse import urlsplit

from flask import current_app

SQLITE_PREFIX = "sqlite:///"
SUBPROCESS_TIMEOUT_SECONDS = 300


def _db_engine():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:"):
        return "sqlite"
    if uri.startswith("postgresql"):
        return "postgresql"
    if uri.startswith("mysql"):
        return "mysql"
    return "unknown"


def _sqlite_path():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith(SQLITE_PREFIX):
        return None
    path = uri[len(SQLITE_PREFIX):]
    if path == ":memory:":
        return None
    return path


def _backups_dir():
    d = current_app.config["BACKUPS_DIR"]
    os.makedirs(d, exist_ok=True)
    return d


def _timestamped_filename(ext):
    return f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"


def _sqlite_backup():
    src = _sqlite_path()
    if src is None:
        raise ValueError(
            "This deployment's sqlite database is in-memory (typical of a test run), so there's "
            "no file to back up."
        )
    if not os.path.exists(src):
        raise ValueError(f"Database file not found at {src}.")

    filename = _timestamped_filename("db")
    dest = os.path.join(_backups_dir(), filename)
    shutil.copy2(src, dest)
    return filename, os.path.getsize(dest)


def _postgres_backup():
    if shutil.which("pg_dump") is None:
        raise ValueError(
            "pg_dump was not found on PATH. Install the PostgreSQL client tools on this server "
            "to enable backups from here, or back up this database using your provider's own "
            "tooling/managed snapshots instead (e.g. RDS/Cloud SQL automated backups)."
        )
    parts = urlsplit(current_app.config["SQLALCHEMY_DATABASE_URI"])
    dbname = (parts.path or "/").lstrip("/")
    if not dbname:
        raise ValueError("Could not determine the database name from SQLALCHEMY_DATABASE_URI.")

    filename = _timestamped_filename("dump")
    dest = os.path.join(_backups_dir(), filename)
    env = os.environ.copy()
    if parts.password:
        env["PGPASSWORD"] = parts.password

    cmd = [
        "pg_dump",
        "-h", parts.hostname or "localhost",
        "-p", str(parts.port or 5432),
        "-U", parts.username or "",
        "-Fc",  # custom format: compressed, restorable with pg_restore
        "-f", dest,
        dbname,
    ]
    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"pg_dump timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds.")
    if result.returncode != 0:
        _remove_partial(dest)
        raise ValueError(f"pg_dump failed: {result.stderr.strip()[:500]}")
    return filename, os.path.getsize(dest)


def _mysql_backup():
    if shutil.which("mysqldump") is None:
        raise ValueError(
            "mysqldump was not found on PATH. Install the MySQL client tools on this server "
            "to enable backups from here, or back up this database using your provider's own "
            "tooling/managed snapshots instead (e.g. RDS/Cloud SQL automated backups)."
        )
    parts = urlsplit(current_app.config["SQLALCHEMY_DATABASE_URI"])
    dbname = (parts.path or "/").lstrip("/")
    if not dbname:
        raise ValueError("Could not determine the database name from SQLALCHEMY_DATABASE_URI.")

    filename = _timestamped_filename("sql")
    dest = os.path.join(_backups_dir(), filename)
    env = os.environ.copy()
    if parts.password:
        # MYSQL_PWD rather than a -p command-line flag, so the password
        # never shows up in `ps` output or a process list.
        env["MYSQL_PWD"] = parts.password

    cmd = [
        "mysqldump",
        "-h", parts.hostname or "localhost",
        "-P", str(parts.port or 3306),
        "-u", parts.username or "root",
        dbname,
    ]
    try:
        with open(dest, "wb") as f:
            result = subprocess.run(
                cmd, env=env, stdout=f, stderr=subprocess.PIPE, timeout=SUBPROCESS_TIMEOUT_SECONDS
            )
    except subprocess.TimeoutExpired:
        _remove_partial(dest)
        raise ValueError(f"mysqldump timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds.")
    if result.returncode != 0:
        _remove_partial(dest)
        raise ValueError(f"mysqldump failed: {result.stderr.decode(errors='replace').strip()[:500]}")
    return filename, os.path.getsize(dest)


def _remove_partial(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def create_backup():
    """Take a backup using whichever method fits the configured database
    engine (see module docstring). Returns (filename, size_bytes) on
    success, or raises ValueError with a human-readable reason the caller
    can flash straight to the user."""
    engine = _db_engine()
    if engine == "sqlite":
        return _sqlite_backup()
    if engine == "postgresql":
        return _postgres_backup()
    if engine == "mysql":
        return _mysql_backup()
    raise ValueError(
        "Automated backups aren't implemented for this database engine. Use that engine's own "
        "backup tooling instead."
    )


_BACKUP_FILENAME_RE = re.compile(r"^backup_\d{8}_\d{6}\.(db|dump|sql)$")


def list_backups():
    """Every backup file currently on disk, newest first — across all
    engines' filename patterns (.db from sqlite, .dump from pg_dump, .sql
    from mysqldump)."""
    d = _backups_dir()
    entries = []
    for name in os.listdir(d):
        if not _BACKUP_FILENAME_RE.match(name):
            continue
        full = os.path.join(d, name)
        entries.append({
            "filename": name,
            "size_bytes": os.path.getsize(full),
            "created_at": datetime.utcfromtimestamp(os.path.getmtime(full)),
        })
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return entries


def delete_backup(filename):
    """Delete one backup file by name. Validates the filename against the
    same pattern create_backup() generates before touching the filesystem,
    so this can never be tricked into deleting anything outside
    BACKUPS_DIR via a crafted '../' path."""
    if not _BACKUP_FILENAME_RE.match(filename):
        raise ValueError("Invalid backup filename.")
    full = os.path.join(_backups_dir(), filename)
    if os.path.exists(full):
        os.remove(full)
