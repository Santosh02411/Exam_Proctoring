import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-this-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'exam_proctoring.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Proctoring settings
    MAX_VIOLATIONS_BEFORE_TERMINATION = int(os.environ.get("MAX_VIOLATIONS", 5))
    SNAPSHOT_UPLOAD_DIR = os.path.join(BASE_DIR, "instance", "snapshots")
    RECORDINGS_DIR = os.path.join(BASE_DIR, "instance", "recordings")
    # face-api.js euclideanDistance threshold below which two descriptors are
    # considered the same person. face-api.js's own docs suggest ~0.6.
    FACE_MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", 0.6))

    # Enhanced identity verification: government/college ID upload + OCR +
    # ID-photo-to-live-face matching (see app.proctoring's id-document
    # routes), and random in-exam identity spot checks (see proctor.js).
    ID_DOCUMENT_UPLOAD_DIR = os.path.join(BASE_DIR, "instance", "id_documents")
    ID_DOCUMENT_MAX_BYTES = 8 * 1024 * 1024  # 8 MB — a phone photo of an ID comfortably fits
    ID_DOCUMENT_ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp"}
    # Random interval bounds (seconds) between in-exam identity spot checks —
    # randomized per attempt so the timing can't be anticipated/gamed.
    IDENTITY_SPOTCHECK_MIN_SECONDS = int(os.environ.get("IDENTITY_SPOTCHECK_MIN_SECONDS", 180))
    IDENTITY_SPOTCHECK_MAX_SECONDS = int(os.environ.get("IDENTITY_SPOTCHECK_MAX_SECONDS", 420))

    # Notifications & Reminders: how far ahead of a test's scheduled
    # start_time to send the "starting soon" reminder — see
    # app.notifications.send_starting_soon_reminders and the `send-reminders`
    # CLI command, which is meant to be run periodically by an external cron.
    EXAM_REMINDER_WINDOW_MINUTES = int(os.environ.get("EXAM_REMINDER_WINDOW_MINUTES", 60))

    # Uploads — raised to comfortably fit ~30s webm video chunks plus base64 JPEG snapshots.
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB

    # Email (verification + password reset). If MAIL_SERVER is unset, emails are
    # written to instance/outbox.log and the link is also flashed directly in the
    # browser, so the whole flow works out of the box with no SMTP server to set up.
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "no-reply@exam-proctoring.local")

    # Per-IP rate limiting on abuse-prone unauthenticated endpoints. Each
    # request (valid or not) counts against the window; once the max is hit,
    # further requests are rejected with a flash message until the window
    # rolls forward. Disable only for local dev/testing — see IpRateLimit.
    RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"
    REGISTER_MAX_PER_IP = int(os.environ.get("REGISTER_MAX_PER_IP", 5))
    REGISTER_WINDOW_MINUTES = int(os.environ.get("REGISTER_WINDOW_MINUTES", 60))
    FORGOT_PASSWORD_MAX_PER_IP = int(os.environ.get("FORGOT_PASSWORD_MAX_PER_IP", 5))
    FORGOT_PASSWORD_WINDOW_MINUTES = int(os.environ.get("FORGOT_PASSWORD_WINDOW_MINUTES", 15))
    RESEND_VERIFICATION_MAX_PER_IP = int(os.environ.get("RESEND_VERIFICATION_MAX_PER_IP", 5))
    RESEND_VERIFICATION_WINDOW_MINUTES = int(os.environ.get("RESEND_VERIFICATION_WINDOW_MINUTES", 15))

    # Password complexity policy, enforced on registration and password reset.
    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", 8))

    # Exam integrity: session/device tracking (see app.security).
    # When on, a new successful login ends every other still-active
    # session for that account, so at most one browser is ever actually
    # logged in at a time — the classic "signed in elsewhere" behavior.
    SINGLE_SESSION_PER_ACCOUNT = os.environ.get("SINGLE_SESSION_PER_ACCOUNT", "true").lower() == "true"

    # Optional VPN/proxy detection on login, via a third-party IP-reputation
    # API. Off by default since it requires an external provider to be
    # configured; app.security.check_vpn_or_proxy() no-ops entirely (never
    # blocks a login) if disabled, unconfigured, or unreachable.
    VPN_DETECTION_ENABLED = os.environ.get("VPN_DETECTION_ENABLED", "false").lower() == "true"
    VPN_DETECTION_API_URL = os.environ.get("VPN_DETECTION_API_URL")
    VPN_DETECTION_API_KEY = os.environ.get("VPN_DETECTION_API_KEY")
    VPN_DETECTION_TIMEOUT_SECONDS = float(os.environ.get("VPN_DETECTION_TIMEOUT_SECONDS", 3))

    # Plagiarism / answer similarity detection (see app.similarity).
    # Default match threshold, as a percentage, for two descriptive/coding
    # answers to the same question to get flagged for examiner review.
    SIMILARITY_THRESHOLD_DEFAULT = float(os.environ.get("SIMILARITY_THRESHOLD_DEFAULT", 70))

    # System Monitoring & Operations (see app.system_ops, app.backup, app.retention).
    BACKUPS_DIR = os.environ.get("BACKUPS_DIR", os.path.join(BASE_DIR, "instance", "backups"))
    # How many days of exam recordings/snapshots/session & log history to
    # keep before app.retention.apply_retention_policies() deletes them —
    # run manually from /ops/retention or periodically via the
    # `flask apply-retention` CLI command (same "no built-in scheduler,
    # bring your own cron" pattern as send-reminders/backup-db).
    RECORDING_RETENTION_DAYS = int(os.environ.get("RECORDING_RETENTION_DAYS", 90))
    SNAPSHOT_RETENTION_DAYS = int(os.environ.get("SNAPSHOT_RETENTION_DAYS", 90))
    ENDED_LOGIN_SESSION_RETENTION_DAYS = int(os.environ.get("ENDED_LOGIN_SESSION_RETENTION_DAYS", 180))
    LOGIN_SECURITY_EVENT_RETENTION_DAYS = int(os.environ.get("LOGIN_SECURITY_EVENT_RETENTION_DAYS", 180))
    ACTIVITY_LOG_RETENTION_DAYS = int(os.environ.get("ACTIVITY_LOG_RETENTION_DAYS", 365))
    NOTIFICATION_LOG_RETENTION_DAYS = int(os.environ.get("NOTIFICATION_LOG_RETENTION_DAYS", 180))
    ERROR_LOG_RETENTION_DAYS = int(os.environ.get("ERROR_LOG_RETENTION_DAYS", 90))

    # Live Proctor Alerts (see app.admin.proctor_alerts_stream): how often
    # the SSE stream polls the database for new high-severity events, and
    # how long a single stream is kept open before it ends and the
    # browser's EventSource reconnects on its own.
    PROCTOR_ALERT_POLL_SECONDS = int(os.environ.get("PROCTOR_ALERT_POLL_SECONDS", 3))
    PROCTOR_ALERT_STREAM_SECONDS = int(os.environ.get("PROCTOR_ALERT_STREAM_SECONDS", 55))

    # Exam Session Device Management (see app.exam_sessions): how long a
    # claimed exam session can go without a heartbeat/autosave before it's
    # considered abandoned (crash, closed tab) and safe for a new tab/
    # device to take over without being treated as a concurrent session.
    EXAM_SESSION_STALE_AFTER_SECONDS = int(os.environ.get("EXAM_SESSION_STALE_AFTER_SECONDS", 45))
    # When on (default), a second tab/device trying to open an exam that's
    # still actively held by another is blocked outright. Turning this off
    # keeps recording device/browser info and rotating session tokens, but
    # never blocks — useful for a deployment that only wants the audit
    # trail, not the enforcement.
    EXAM_SESSION_ENFORCE_SINGLE_SESSION = os.environ.get("EXAM_SESSION_ENFORCE_SINGLE_SESSION", "true").lower() == "true"

    # LMS/API Integrations (see app.api_v1). Timeout for the optional
    # outbound result-published webhook (Organization.lms_webhook_url) —
    # kept short since this fires inline on the result-publish request
    # path and must never let a slow/unreachable receiver hang it.
    LMS_WEBHOOK_TIMEOUT_SECONDS = float(os.environ.get("LMS_WEBHOOK_TIMEOUT_SECONDS", 4))

    # Real-time system health alerts (see app.alerting). Disk usage is
    # checked whenever /ops health-related pages load and via the
    # `flask check-health-alerts` CLI command (meant for an external cron,
    # same pattern as backup-db/apply-retention/send-reminders); the error
    # rate is additionally checked inline, right after every error is
    # logged, so a spike triggers a notification without waiting for cron.
    DISK_USAGE_ALERT_THRESHOLD_PCT = float(os.environ.get("DISK_USAGE_ALERT_THRESHOLD_PCT", 90))
    ERROR_RATE_ALERT_THRESHOLD = int(os.environ.get("ERROR_RATE_ALERT_THRESHOLD", 10))
    ERROR_RATE_ALERT_WINDOW_MINUTES = int(os.environ.get("ERROR_RATE_ALERT_WINDOW_MINUTES", 15))
    # Comma-separated override for alert recipients; defaults to every
    # super_admin account's email when unset.
    ALERT_EMAIL_OVERRIDE = os.environ.get("ALERT_EMAIL_OVERRIDE")
    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

    # Terms of Service / Privacy Policy (see app.legal). Bumping this forces
    # nothing retroactively — it's just recorded against each user's
    # acceptance (User.terms_version_accepted) so you can tell who agreed
    # to which version if the policy text changes later.
    TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-08-28")

    # Org-level branding (see app.branding): each organization can upload
    # its own logo and set a primary accent color, applied to their users'
    # UI. Logos are stored on disk, one per org, under this directory.
    ORG_BRANDING_DIR = os.environ.get("ORG_BRANDING_DIR", os.path.join(BASE_DIR, "instance", "org_branding"))
    ORG_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB is plenty for a logo
    ORG_LOGO_ALLOWED_EXTS = {"png", "jpg", "jpeg", "svg", "webp"}
