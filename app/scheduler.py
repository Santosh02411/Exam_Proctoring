"""Background jobs — safe with any number of web workers.

The old design started a daemon thread inside EVERY process that imported the
app, so `gunicorn --workers 3` ran the reminder sweep three times per
interval (and Flask's reloader, a second copy of the app, etc.). Two things
fix that, and both are needed:

1. Atomic job claiming (JobLease). Before a job runs, the process must win a
   single conditional UPDATE on its lease row
   (`UPDATE job_leases SET locked_until=... WHERE name=? AND locked_until<=now`).
   The database guarantees exactly one UPDATE sees rowcount==1 per window, no
   matter how many processes race — across threads, Gunicorn workers and
   containers alike. The lease is held for ~the job's interval, so the
   others' ticks in that window are no-ops.

2. Duplicate-notification prevention (NotificationClaim, in
   app.notifications). Even if a job were somehow run twice, each
   (student, test, kind) reminder is *claimed* by inserting a UNIQUE row
   before it is sent, so it can only ever be sent once.

Where the loop runs is a deployment choice (SCHEDULER_MODE):
  external (production default) — web workers run NO scheduler; run exactly
      one dedicated process: `flask run-scheduler` (see docker-compose.yml).
  embedded (development default) — each process runs a thread; safe thanks
      to the lease, but wasteful with many workers.
  off — nothing runs (use `flask run-scheduler --once` from cron).
"""

import os
import signal
import socket
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import JobLease

REMINDER_JOB = "starting-soon-reminders"


def owner_id():
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def resolve_mode(app):
    """external | embedded | off — explicit SCHEDULER_MODE wins; otherwise
    embedded in development and external in production. The legacy
    ENABLE_BACKGROUND_SCHEDULER=false still means off."""
    if not app.config.get("ENABLE_BACKGROUND_SCHEDULER", True) or app.config.get("TESTING"):
        return "off"
    mode = str(app.config.get("SCHEDULER_MODE") or "").strip().lower()
    if mode in ("external", "embedded", "off"):
        return mode
    return "embedded" if app.config.get("APP_ENV") == "development" else "external"


def interval_seconds(app):
    configured = int(app.config.get("REMINDER_SCHEDULER_INTERVAL_SECONDS", 300))
    interval = max(configured, 5)
    if interval != configured:
        app.logger.warning(
            "scheduler: REMINDER_SCHEDULER_INTERVAL_SECONDS=%s is below the 5s floor; using 5s", configured
        )
    return interval


def claim_lease(name, owner, ttl_seconds, now=None):
    """Try to become the one runner of job `name` for the next ttl_seconds.
    True iff THIS caller won. Safe under any concurrency: the claim is one
    conditional UPDATE, and the row is created with an insert whose unique
    primary key makes a simultaneous double-create fail harmlessly."""
    now = now or datetime.utcnow()
    if db.session.get(JobLease, name) is None:
        try:
            db.session.add(JobLease(name=name, locked_until=datetime(1970, 1, 1)))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()  # another process created it first — fine
    won = (
        db.session.query(JobLease)
        .filter(JobLease.name == name, JobLease.locked_until <= now)
        .update(
            {"locked_by": owner, "locked_until": now + timedelta(seconds=ttl_seconds), "last_started_at": now},
            synchronize_session=False,
        )
    )
    db.session.commit()
    return won == 1


def record_result(name, owner, status):
    db.session.query(JobLease).filter(JobLease.name == name, JobLease.locked_by == owner).update(
        {"last_finished_at": datetime.utcnow(), "last_status": (status or "")[:300]}, synchronize_session=False
    )
    db.session.commit()


def run_job_once(app, name, func, interval):
    """Claim `name` and, only if won, run func(). Returns (ran, result).
    The lease is held for 80% of the interval (not released on completion), so
    other workers' timers firing in the same window cannot re-run it."""
    with app.app_context():
        owner = owner_id()
        ttl = max(int(interval * 0.8), 1)
        if not claim_lease(name, owner, ttl):
            return False, None
        try:
            result = func()
            record_result(name, owner, f"ok: {result}")
            return True, result
        except Exception as exc:
            db.session.rollback()
            record_result(name, owner, f"error: {type(exc).__name__}: {exc}")
            app.logger.exception("scheduler: job %s failed", name)
            return True, None


def _reminder_job(app):
    def run():
        from app.notifications import send_starting_soon_reminders

        # notify()/url_for(_external=True) need a request context even outside a request.
        with app.test_request_context():
            sent = send_starting_soon_reminders()
        if sent:
            app.logger.info("scheduler: sent %d starting-soon reminder(s)", sent)
        return sent

    return run


def tick(app):
    """One scheduler pass over every periodic job."""
    return run_job_once(app, REMINDER_JOB, _reminder_job(app), interval_seconds(app))


def run_forever(app, stop_event=None):
    """The dedicated scheduler loop (`flask run-scheduler`). Stops cleanly on
    SIGTERM/SIGINT."""
    stop = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    interval = interval_seconds(app)
    app.logger.info("scheduler: running (every %ds) as %s", interval, owner_id())
    while not stop.is_set():
        tick(app)
        stop.wait(interval)
    app.logger.info("scheduler: stopped")


def start_background_scheduler(app):
    """Called from create_app. Starts the in-process thread ONLY in embedded
    mode (and not in the Flask reloader's watcher process). In external/off
    mode this does nothing — a dedicated process runs the jobs."""
    mode = resolve_mode(app)
    if mode != "embedded":
        if mode == "external":
            app.logger.info("scheduler: external mode — this process does not run background jobs "
                            "(run `flask run-scheduler` as a single dedicated process)")
        return None
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None  # the reloader's watcher process; the reloaded child starts it

    interval = interval_seconds(app)

    def _loop():
        while True:
            time.sleep(interval)
            tick(app)  # lease-guarded: duplicate threads/workers cannot double-run

    thread = threading.Thread(target=_loop, name="embedded-scheduler", daemon=True)
    thread.interval = interval
    thread.start()
    app.logger.info("scheduler: embedded thread started (every %ds, lease-guarded)", interval)
    return thread
