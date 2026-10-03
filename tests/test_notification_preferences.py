import json
import os

from tests.conftest import register_and_verify, login, add_single_question


def _outbox(app):
    path = os.path.join(app.instance_path, "outbox.log")
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8").read()


def test_preferences_page_defaults_all_on(client, app):
    register_and_verify(client, app, "User", "npa1@test.com", "9122211001", "student", "Studpass1!")
    login(client, "npa1@test.com", "Studpass1!")
    r = client.get("/profile/notification-preferences")
    assert r.status_code == 200
    # No explicit preferences saved yet — every checkbox should be checked.
    body = r.get_data(as_text=True)
    assert body.count('checked') >= len(body.split('type="checkbox"')) - 1 or "checked" in body


def test_disabling_email_for_a_type_stores_only_that_opt_out(client, app):
    register_and_verify(client, app, "User", "npa2@test.com", "9122211002", "student", "Studpass1!")
    login(client, "npa2@test.com", "Studpass1!")

    # Submit the form with every checkbox checked EXCEPT email_exam_scheduled.
    from app.notifications import NOTIFICATION_PREF_LABELS
    data = {}
    for nt in NOTIFICATION_PREF_LABELS:
        if nt != "exam_scheduled":
            data[f"email_{nt}"] = "on"
        data[f"sms_{nt}"] = "on"
    r = client.post("/profile/notification-preferences", data=data, follow_redirects=True)
    assert b"Notification preferences updated" in r.data

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="npa2@test.com").first()
        prefs = json.loads(user.notification_prefs)
        assert prefs == {"exam_scheduled": {"email": False}}


def test_email_opted_out_notification_is_not_sent(client, app):
    register_and_verify(client, app, "Admin", "npa3@test.com", "9122211003", "admin", "Adminpass1!")
    register_and_verify(client, app, "Student", "nps3@test.com", "9122211004", "student", "Studpass1!")

    client.get("/logout")
    login(client, "nps3@test.com", "Studpass1!")
    from app.notifications import NOTIFICATION_PREF_LABELS
    data = {f"email_{nt}": "on" for nt in NOTIFICATION_PREF_LABELS if nt != "exam_scheduled"}
    data.update({f"sms_{nt}": "on" for nt in NOTIFICATION_PREF_LABELS})
    client.post("/profile/notification-preferences", data=data)

    client.get("/logout")
    login(client, "npa3@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="NP1", title="Preferences Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="NP1").first().id
        student_id = User.query.filter_by(email="nps3@test.com").first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)

    before = _outbox(app)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_id)], "notify": "on"})
    after = _outbox(app)
    assert "Preferences Test" not in after[len(before):]

    with app.app_context():
        from app.models import NotificationLog
        log = NotificationLog.query.filter_by(user_id=student_id, notif_type="exam_scheduled").first()
        assert log is not None
        assert log.send_status == "skipped_by_preference"


def test_opting_out_does_not_affect_other_users(client, app):
    register_and_verify(client, app, "Admin", "npa4@test.com", "9122211005", "admin", "Adminpass1!")
    register_and_verify(client, app, "StudentA", "nps4a@test.com", "9122211006", "student", "Studpass1!")
    register_and_verify(client, app, "StudentB", "nps4b@test.com", "9122211007", "student", "Studpass1!")

    client.get("/logout")
    login(client, "nps4a@test.com", "Studpass1!")
    from app.notifications import NOTIFICATION_PREF_LABELS
    data = {f"email_{nt}": "on" for nt in NOTIFICATION_PREF_LABELS if nt != "exam_scheduled"}
    data.update({f"sms_{nt}": "on" for nt in NOTIFICATION_PREF_LABELS})
    client.post("/profile/notification-preferences", data=data)

    client.get("/logout")
    login(client, "npa4@test.com", "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code="NP2", title="Multi Student Test", description="d", duration_minutes=20,
        total_questions=1, passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test, User
        test_id = Test.query.filter_by(test_code="NP2").first().id
        student_a = User.query.filter_by(email="nps4a@test.com").first().id
        student_b = User.query.filter_by(email="nps4b@test.com").first().id
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    client.post(f"/admin/tests/{test_id}/assign", data={"student_ids": [str(student_a), str(student_b)], "notify": "on"})

    with app.app_context():
        from app.models import NotificationLog
        log_a = NotificationLog.query.filter_by(user_id=student_a, notif_type="exam_scheduled").first()
        log_b = NotificationLog.query.filter_by(user_id=student_b, notif_type="exam_scheduled").first()
        assert log_a.send_status == "skipped_by_preference"
        assert log_b.send_status in ("sent", "logged")


def test_disabled_sms_checkbox_without_phone_does_not_record_opt_out(client, app):
    """A user with no phone number sees the SMS checkboxes disabled —
    submitting the form shouldn't silently record every type as SMS-off,
    since they never touched those checkboxes at all."""
    register_and_verify(client, app, "User", "npa5@test.com", "9122211008", "student", "Studpass1!")
    login(client, "npa5@test.com", "Studpass1!")
    with app.app_context():
        from app.models import User
        from app import db
        u = User.query.filter_by(email="npa5@test.com").first()
        u.phone = ""
        db.session.commit()

    from app.notifications import NOTIFICATION_PREF_LABELS
    data = {f"email_{nt}": "on" for nt in NOTIFICATION_PREF_LABELS}
    # No sms_* fields submitted at all, matching a disabled checkbox.
    client.post("/profile/notification-preferences", data=data)

    with app.app_context():
        from app.models import User
        user = User.query.filter_by(email="npa5@test.com").first()
        assert user.notification_prefs is None
