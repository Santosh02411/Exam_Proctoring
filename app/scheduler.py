"""A tiny, dependency-free background scheduler for exam-starting-soon
reminders (see app.notifications.send_starting_soon_reminders).

Why this exists: that function has always existed and worked correctly, but
until now the only ways to trigger it were an admin clicking "Send
Starting-Soon Reminders Now", or an external cron/Task Scheduler entry
calling `flask send-reminders` — nothing in the app itself ever called it on
its own. On a deployment without that external cron set up (the common
case for anyone who just runs the app), the result is that the "reminder
1 hour before the test" feature silently never fires in real time, even
though every piece of it works once triggered. This module closes that gap
by running the same sweep on a periodic background thread inside the app
process itself, so reminders go out automatically without any extra
infrastructure.

Deliberately NOT a dependency (no APScheduler, no Celery): one daemon
thread, one sleep loop, one try/except so a bad sweep never kills it.
"""

import os
import threading
import time


def start_background_scheduler(app):
    """Start the reminder sweep loop, unless explicitly disabled or running
    in a context where it would be wrong to (tests, or the watcher half of
    Flask's debug-mode auto-reloader, which would otherwise start this
    twice — once in the watcher process and once in the reloaded child)."""
    if not app.config.get("ENABLE_BACKGROUND_SCHEDULER", True):
        return
    if app.config.get("TESTING"):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        # Debug mode's reloader: this is the watcher process: the actual
        # app code (and this thread) belongs in the reloaded child only.
        return

    configured_interval = int(app.config.get("REMINDER_SCHEDULER_INTERVAL_SECONDS", 300))
    # A floor only against a genuine misconfiguration (0, a negative number,
    # or a typo) that would otherwise spin this loop in a tight busy-cycle —
    # NOT a silent override of a deliberately short interval. A mismatch
    # between what was configured and what actually runs is exactly the
    # kind of "the time is wrong" surprise this file exists to prevent, so
    # if clamping happens at all, it's logged, never silent.
    interval = max(configured_interval, 5)
    if interval != configured_interval:
        app.logger.warning(
            "background scheduler: REMINDER_SCHEDULER_INTERVAL_SECONDS=%s is below the 5s floor; using 5s instead",
            configured_interval,
        )

    def _loop():
        while True:
            time.sleep(interval)
            try:
                from app.notifications import send_starting_soon_reminders
                # notify()/url_for(_external=True) need a request context
                # even outside a real request, same as the CLI command.
                with app.test_request_context():
                    sent = send_starting_soon_reminders()
                if sent:
                    app.logger.info("background scheduler: sent %d starting-soon reminder(s)", sent)
            except Exception:
                # A bad sweep (a DB hiccup, a transient SMTP failure inside
                # notify() already catches its own errors) must never kill
                # this thread — there's no supervisor to restart it.
                app.logger.exception("background scheduler: reminder sweep failed")

    thread = threading.Thread(target=_loop, name="reminder-scheduler", daemon=True)
    thread.interval = interval  # exposed (mainly for tests) so "what interval is this actually using" is never a guess
    thread.start()
    app.logger.info("background scheduler: started (every %ds)", interval)
    return thread
