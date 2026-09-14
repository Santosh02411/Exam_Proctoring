import os
from datetime import datetime

from flask import current_app


def send_sms(to_number, body):
    """Send an SMS if Twilio is configured; otherwise write it to
    instance/sms_outbox.log — the exact same dev-mode fallback pattern as
    app.email_utils.send_email, so SMS-eligible notifications (see
    app.notifications.notify) work end-to-end and are inspectable even
    with no Twilio account set up. Returns "twilio" or "logged" — never
    raises; app.notifications.notify already wraps this in its own
    try/except and records the outcome in NotificationLog, so a delivery
    failure here shouldn't also crash whatever request triggered it."""
    account_sid = current_app.config.get("TWILIO_ACCOUNT_SID")
    auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    from_number = current_app.config.get("TWILIO_FROM_NUMBER")

    if account_sid and auth_token and from_number:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        # A WhatsApp-enabled sender (from_number written as
        # "whatsapp:+14155238886") requires the recipient number to carry
        # the same "whatsapp:" prefix too — Twilio's API rejects a bare
        # phone number as the `to` when `from` is a WhatsApp sender.
        to = to_number if not from_number.startswith("whatsapp:") or to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        client.messages.create(to=to, from_=from_number, body=body)
        return "twilio"

    # Fallback: no Twilio configured — log the message instead of sending it.
    outbox_path = os.path.join(current_app.instance_path, "sms_outbox.log")
    with open(outbox_path, "a", encoding="utf-8") as f:
        f.write(f"\n--- {datetime.utcnow().isoformat()} ---\nTo: {to_number}\n\n{body}\n")
    current_app.logger.info("SMS (no Twilio configured, logged to sms_outbox.log): to=%s", to_number)
    return "logged"
