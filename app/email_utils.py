import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from flask import current_app
from itsdangerous import URLSafeTimedSerializer


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_token(email, salt):
    return _serializer().dumps(email, salt=salt)


def verify_token(token, salt, max_age_seconds=86400):
    try:
        return _serializer().loads(token, salt=salt, max_age=max_age_seconds)
    except Exception:
        return None


def send_email(to_address, subject, body):
    """Send an email if SMTP is configured; otherwise write it to
    instance/outbox.log and print it to the server console. This means the
    verification / password-reset flow works end-to-end even with no mail
    server set up — the caller should also surface the link directly in the
    browser response for convenience in that fallback case.
    """
    mail_server = current_app.config.get("MAIL_SERVER")

    if mail_server:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = current_app.config["MAIL_SENDER"]
        msg["To"] = to_address

        with smtplib.SMTP(mail_server, current_app.config["MAIL_PORT"]) as server:
            if current_app.config.get("MAIL_USE_TLS"):
                server.starttls()
            username = current_app.config.get("MAIL_USERNAME")
            password = current_app.config.get("MAIL_PASSWORD")
            if username and password:
                server.login(username, password)
            server.sendmail(current_app.config["MAIL_SENDER"], [to_address], msg.as_string())
        return "smtp"

    # Fallback: no SMTP configured — log the email instead of sending it.
    outbox_path = os.path.join(current_app.instance_path, "outbox.log")
    with open(outbox_path, "a", encoding="utf-8") as f:
        f.write(f"\n--- {datetime.utcnow().isoformat()} ---\nTo: {to_address}\nSubject: {subject}\n\n{body}\n")
    current_app.logger.info("MAIL (no SMTP configured, logged to outbox.log): to=%s subject=%s", to_address, subject)
    return "logged"
