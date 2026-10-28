import click

from app import db
from app.models import User, Organization


def _require_strong_password(password):
    """Same complexity rules as the web forms (8+ chars, upper, lower, digit,
    special) so a CLI-created admin can't be given a trivially weak password."""
    import re

    problems = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[a-z]", password):
        problems.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("a special character")
    if problems:
        raise click.ClickException("Password too weak — it needs " + ", ".join(problems) + ".")


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Removed: schema is managed by migrations now."""
        raise click.ClickException(
            "init-db has been removed — the schema is managed by Alembic migrations.\n"
            "  New database:      flask db upgrade\n"
            "  Existing database: flask db stamp 0001_baseline && flask db upgrade   (back it up first)\n"
            "  Check status:      flask schema-status"
        )

    @app.cli.command("schema-status")
    def schema_status_cmd():
        """Show the database's migration revision vs. the code's latest, and
        exit non-zero if they differ."""
        from app.db_schema import schema_status

        current, head = schema_status(app)
        click.echo(f"database revision: {current or '(none — unmigrated database)'}")
        click.echo(f"latest revision:   {head}")
        if current != head:
            raise click.ClickException("Database is NOT up to date. Run: flask db upgrade")
        click.echo("OK — database is up to date.")

    @app.cli.command("seed-admin")
    @click.option("--name", default="Admin User")
    @click.option("--email", prompt=True)
    # No default password: a well-known default ("Admin123!") is a published
    # credential. Omit --password to be prompted (input hidden).
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--phone", default="9999999999")
    @click.option("--org", default="Default Organization", help="Organization this admin belongs to (created if it doesn't exist).")
    def seed_admin(name, email, password, phone, org):
        """Create a default admin account (under the given organization,
        creating it if needed) if the account doesn't exist yet."""
        _require_strong_password(password)
        if User.query.filter_by(email=email).first():
            click.echo(f"User {email} already exists.")
            return
        from app.models import gen_user_id
        from app.auth import _unique_slug

        organization = Organization.query.filter_by(name=org).first()
        if not organization:
            organization = Organization(name=org, slug=_unique_slug(org), status="active")
            db.session.add(organization)
            db.session.flush()

        user = User(
            user_id=gen_user_id("admin"),
            name=name,
            email=email,
            phone=phone,
            role="admin",
            status="active",
            email_verified=True,
            org_id=organization.id,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin created: {email} / {password} (organization: {organization.name})")

    @app.cli.command("create-super-admin")
    @click.option("--name", default="Platform Admin")
    @click.option("--email", required=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--phone", default="9999999999")
    def create_super_admin(name, email, password, phone):
        """Create a platform-level super_admin account — manages
        Organizations (tenants) themselves, not any one org's exams. Not
        reachable via public registration by design, so this is the only
        way to create one."""
        _require_strong_password(password)
        if User.query.filter_by(email=email).first():
            click.echo(f"User {email} already exists.")
            return
        from app.models import gen_user_id

        user = User(
            user_id=gen_user_id("sup"),
            name=name,
            email=email,
            phone=phone,
            role="super_admin",
            status="active",
            email_verified=True,
            org_id=None,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Super admin created: {email}")

    @app.cli.command("backup-db")
    def backup_db():
        """Take a one-off database backup (see app.backup) — meant to be run
        periodically by an external cron/Task Scheduler entry, the same way
        send-reminders is."""
        from app import backup

        with app.app_context():
            try:
                filename, size = backup.create_backup()
            except ValueError as exc:
                click.echo(f"Backup failed: {exc}")
                return
        click.echo(f"Backup created: {filename} ({size} bytes)")

    @app.cli.command("apply-retention")
    def apply_retention():
        """Run every configured data-retention policy once (see
        app.retention) — deletes recordings/snapshots/sessions/logs past
        their configured retention window. Meant to be run periodically by
        an external cron/Task Scheduler entry."""
        from app import retention

        with app.app_context():
            results = retention.apply_retention_policies()
        for category, count in results.items():
            click.echo(f"{category}: {count} deleted")

    @app.cli.command("check-health-alerts")
    def check_health_alerts():
        """Run the periodic system-health checks once (see app.alerting) —
        disk usage and error rate — and email/Slack-notify for anything
        newly over threshold. Meant to be run periodically by an external
        cron/Task Scheduler entry; error-rate spikes are also checked
        in real time whenever an error is logged, so this mainly covers
        disk usage."""
        from app import alerting

        with app.app_context():
            new_alerts = alerting.run_health_checks()
        if new_alerts:
            for a in new_alerts:
                click.echo(f"ALERT [{a.severity}] {a.alert_type}: {a.message}")
        else:
            click.echo("No new alerts.")

    @app.cli.command("run-scheduler")
    @click.option("--once", is_flag=True, help="Run one pass and exit (for cron) instead of looping forever.")
    def run_scheduler(once):
        """The dedicated background-job process. Run EXACTLY ONE of these per
        deployment (the web workers run no jobs in production). Jobs are
        lease-claimed and reminders are claim-deduplicated, so an accidental
        second copy is harmless — just wasteful."""
        from app import scheduler

        if once:
            ran, result = scheduler.tick(app)
            click.echo(f"scheduler pass: {'ran' if ran else 'skipped (another process holds the lease)'}"
                       + (f", sent {result} reminder(s)" if result else ""))
            return
        scheduler.run_forever(app)

    @app.cli.command("send-reminders")
    @click.option("--window-minutes", default=None, type=int,
                  help="How far ahead of a test's start_time to send reminders (defaults to EXAM_REMINDER_WINDOW_MINUTES).")
    def send_reminders(window_minutes):
        """Send 'exam starting soon' reminders for tests whose start_time is
        coming up. Meant to be run periodically by an external cron/Task
        Scheduler entry — the app itself has no background scheduler."""
        from app.notifications import send_starting_soon_reminders

        # notify()/url_for(_external=True) need a request context even
        # outside a real request, since SERVER_NAME isn't configured.
        with app.test_request_context():
            sent = send_starting_soon_reminders(window_minutes)
        click.echo(f"Sent {sent} starting-soon reminder(s).")
