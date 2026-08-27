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
