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
