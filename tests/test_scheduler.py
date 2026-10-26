"""The in-process background reminder scheduler (app/scheduler.py) — it must
stay off during tests (see TestConfig.ENABLE_BACKGROUND_SCHEDULER), but the
decision logic itself (would it start, for a given config?) is worth pinning
down directly rather than only ever exercising the "off" path."""
import threading

from app.scheduler import start_background_scheduler


class _FakeLogger:
    def info(self, *a, **k): pass
    def exception(self, *a, **k): pass
    def warning(self, *a, **k): pass


class _FakeApp:
    """Minimal stand-in so we don't need a real Flask app (and its request
    context machinery) just to test the startup decision and loop body."""
    def __init__(self, config, debug=False):
        self.config = config
        self.debug = debug
        self.logger = _FakeLogger()
        self.test_request_context_calls = 0

    def test_request_context(self):
        app = self

        class _Ctx:
            def __enter__(self):
                app.test_request_context_calls += 1
                return self
            def __exit__(self, *exc):
                return False
        return _Ctx()


def test_disabled_by_config_does_not_start_a_thread():
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": False})
    before = threading.active_count()
    result = start_background_scheduler(app)
    assert result is None
    assert threading.active_count() == before


def test_testing_flag_prevents_startup_even_if_enabled():
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "TESTING": True})
    assert start_background_scheduler(app) is None


def test_debug_watcher_process_is_skipped(monkeypatch):
    """Flask's reloader runs the app module twice in debug mode; only the
    child sets WERKZEUG_RUN_MAIN=true. The watcher half must not also start
    a scheduler thread, or reminders would fire twice as often."""
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True}, debug=True)
    assert start_background_scheduler(app) is None


def test_debug_reloaded_child_process_does_start(monkeypatch):
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "REMINDER_SCHEDULER_INTERVAL_SECONDS": 9999}, debug=True)
    thread = start_background_scheduler(app)
    assert thread is not None and thread.is_alive() and thread.daemon
    assert thread.name == "reminder-scheduler"


def test_non_debug_production_style_app_starts_normally():
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "REMINDER_SCHEDULER_INTERVAL_SECONDS": 9999}, debug=False)
    thread = start_background_scheduler(app)
    assert thread is not None and thread.is_alive()


def test_a_deliberately_short_interval_is_honored_not_silently_overridden():
    """Regression: this used to silently clamp anything below 30s up to 30s
    with no indication it had happened -- so a configured
    REMINDER_SCHEDULER_INTERVAL_SECONDS=10 actually ran every 30s instead,
    and nothing about the app said so. An interval above the genuine-abuse
    floor (5s) must run at exactly what was configured."""
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "REMINDER_SCHEDULER_INTERVAL_SECONDS": 10})
    thread = start_background_scheduler(app)
    assert thread.interval == 10, thread.interval


def test_default_interval_is_honored():
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True})  # no REMINDER_SCHEDULER_INTERVAL_SECONDS set
    thread = start_background_scheduler(app)
    assert thread.interval == 300


def test_only_a_genuinely_degenerate_interval_is_floored_and_a_warning_is_logged():
    calls = []
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "REMINDER_SCHEDULER_INTERVAL_SECONDS": 0})
    app.logger.warning = lambda *a, **k: calls.append((a, k))
    thread = start_background_scheduler(app)
    assert thread.interval == 5, thread.interval
    assert len(calls) == 1 and "0" in str(calls[0])


def test_negative_interval_is_also_floored():
    app = _FakeApp({"ENABLE_BACKGROUND_SCHEDULER": True, "REMINDER_SCHEDULER_INTERVAL_SECONDS": -10})
    thread = start_background_scheduler(app)
    assert thread.interval == 5
