"""Background jobs must be safe with many workers: atomic lease claiming plus
claim-before-send notification dedup (see app/scheduler.py)."""
import threading
from datetime import datetime, timedelta

from tests.conftest import TestConfig, register_and_verify, create_staff


# ---------- mode / interval decisions (no database needed) ----------
class _Log:
    def __init__(self): self.warnings = []
    def info(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def warning(self, *a, **k): self.warnings.append(a)


class _App:
    def __init__(self, config, debug=False):
        self.config, self.debug, self.logger = config, debug, _Log()


def test_testing_and_disabled_flags_mean_off():
    from app.scheduler import resolve_mode
    assert resolve_mode(_App({"TESTING": True, "SCHEDULER_MODE": "embedded"})) == "off"
    assert resolve_mode(_App({"ENABLE_BACKGROUND_SCHEDULER": False, "SCHEDULER_MODE": "embedded"})) == "off"


def test_default_mode_is_external_in_production_and_embedded_in_development():
    from app.scheduler import resolve_mode
    assert resolve_mode(_App({"APP_ENV": "production"})) == "external"
    assert resolve_mode(_App({"APP_ENV": "development"})) == "embedded"
    assert resolve_mode(_App({"APP_ENV": "production", "SCHEDULER_MODE": "embedded"})) == "embedded"


def test_web_workers_in_external_mode_start_no_thread():
    from app.scheduler import start_background_scheduler
    before = threading.active_count()
    assert start_background_scheduler(_App({"APP_ENV": "production"})) is None
    assert threading.active_count() == before


def test_embedded_mode_starts_a_daemon_thread_and_honors_interval(monkeypatch):
    from app.scheduler import start_background_scheduler
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    app = _App({"SCHEDULER_MODE": "embedded", "REMINDER_SCHEDULER_INTERVAL_SECONDS": 9999})
    thread = start_background_scheduler(app)
    assert thread is not None and thread.daemon and thread.interval == 9999


def test_debug_reloader_watcher_is_skipped(monkeypatch):
    from app.scheduler import start_background_scheduler
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    assert start_background_scheduler(_App({"SCHEDULER_MODE": "embedded"}, debug=True)) is None


def test_degenerate_interval_is_floored_with_a_warning():
    from app.scheduler import interval_seconds
    app = _App({"REMINDER_SCHEDULER_INTERVAL_SECONDS": 0})
    assert interval_seconds(app) == 5 and app.logger.warnings
    assert interval_seconds(_App({"REMINDER_SCHEDULER_INTERVAL_SECONDS": 10})) == 10


# ---------- atomic claiming against a real (file) database ----------
def _file_app(tmp_path):
    from app import create_app, db

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'sched.db'}"

    app = create_app(config_object=Cfg())
    with app.app_context():
        db.create_all()
    return app


def test_exactly_one_of_many_racing_workers_wins_the_lease(tmp_path):
    from app.scheduler import claim_lease

    app = _file_app(tmp_path)
    results, barrier = [], threading.Barrier(8)

    def worker(i):
        with app.app_context():
            barrier.wait()
            results.append(claim_lease("job-x", f"w{i}", 60))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count(True) == 1, results


def test_lease_can_be_reclaimed_after_it_expires(tmp_path):
    from app.scheduler import claim_lease

    app = _file_app(tmp_path)
    with app.app_context():
        t0 = datetime.utcnow()
        assert claim_lease("job-y", "a", 60, now=t0)
        assert not claim_lease("job-y", "b", 60, now=t0 + timedelta(seconds=30))
        assert claim_lease("job-y", "b", 60, now=t0 + timedelta(seconds=61))


def test_job_runs_once_per_window_even_if_every_worker_ticks(tmp_path):
    from app.scheduler import run_job_once

    app = _file_app(tmp_path)
    runs, barrier = [], threading.Barrier(6)

    def worker():
        barrier.wait()
        ran, _ = run_job_once(app, "sweep", lambda: runs.append(1) or 7, interval=300)
        return ran

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(runs) == 1


def test_a_failing_job_is_recorded_and_does_not_raise(tmp_path):
    from app.models import JobLease
    from app.scheduler import run_job_once

    app = _file_app(tmp_path)

    def boom():
        raise RuntimeError("kaboom")

    ran, result = run_job_once(app, "bad", boom, interval=60)
    assert ran and result is None
    with app.app_context():
        assert "kaboom" in JobLease.query.get("bad").last_status


def test_run_scheduler_once_cli_skips_when_lease_is_held(tmp_path):
    from app import db
    from app.scheduler import claim_lease, REMINDER_JOB

    app = _file_app(tmp_path)
    with app.app_context():
        assert claim_lease(REMINDER_JOB, "someone-else", 600)
    out = app.test_cli_runner().invoke(args=["run-scheduler", "--once"]).output
    assert "skipped" in out


# ---------- duplicate-notification prevention ----------
def _starting_soon_setup(app, client, n_students=3):
    from app import db
    from app.models import Test, TestEligibility, User

    org_admin = create_staff(app, "Adm", "adm@test.com", "9000000001", "admin", "Adminpass1!")
    ids = []
    for i in range(n_students):
        register_and_verify(client, app, f"S{i}", f"s{i}@test.com", f"90000001{i:02d}", "student", "Studpass1!")
    with app.app_context():
        admin = User.query.get(org_admin)
        test = Test(title="Soon", test_code="SOON1", org_id=admin.org_id, created_by=admin.id, status="published",
                    duration_minutes=30, total_questions=1, passing_marks=1,
                    start_time=datetime.utcnow() + timedelta(minutes=20))
        db.session.add(test)
        db.session.flush()
        for u in User.query.filter_by(role="student").all():
            db.session.add(TestEligibility(test_id=test.id, student_id=u.id))
        db.session.commit()
        return test.id


def test_each_student_gets_exactly_one_reminder_even_when_sweeps_race(tmp_path):
    from app import create_app, db
    from app.models import NotificationLog, Test, TestEligibility, User
    from app.notifications import send_starting_soon_reminders

    app = _file_app(tmp_path)
    client = app.test_client()
    _starting_soon_setup(app, client, n_students=4)

    totals, barrier = [], threading.Barrier(5)

    def sweep():
        with app.app_context():
            with app.test_request_context():
                barrier.wait()
                totals.append(send_starting_soon_reminders())

    threads = [threading.Thread(target=sweep) for _ in range(5)]
    [t.start() for t in threads]; [t.join() for t in threads]

    with app.app_context():
        logs = NotificationLog.query.filter_by(notif_type="exam_starting_soon", channel="email").all()
        per_student = {}
        for l in logs:
            per_student[l.user_id] = per_student.get(l.user_id, 0) + 1
    assert sum(totals) == 4, totals
    assert len(per_student) == 4 and set(per_student.values()) == {1}


def test_a_second_sweep_sends_nothing_new(app, client):
    from app.notifications import send_starting_soon_reminders

    _starting_soon_setup(app, client, n_students=2)
    with app.app_context(), app.test_request_context():
        assert send_starting_soon_reminders() == 2
        assert send_starting_soon_reminders() == 0


def test_a_failed_send_releases_the_claim_so_it_can_be_retried(app, client, monkeypatch):
    import app.notifications as notifications
    from app.models import NotificationClaim

    _starting_soon_setup(app, client, n_students=1)
    real = notifications.notify

    def exploding(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notifications, "notify", exploding)
    with app.app_context(), app.test_request_context():
        try:
            notifications.send_starting_soon_reminders()
        except RuntimeError:
            pass
        assert NotificationClaim.query.count() == 0       # claim given back
        monkeypatch.setattr(notifications, "notify", real)
        assert notifications.send_starting_soon_reminders() == 1   # retried successfully
