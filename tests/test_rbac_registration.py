"""Administrator-registration security: public sign-up makes students only;
privileged accounts exist only via invitation (or the approval-gated
organization request, or the CLI)."""
import re
from datetime import datetime, timedelta

import pytest

from tests.conftest import (
    create_staff, get_last_otp, get_outbox, login, register_and_verify,
)


def _user(app, email):
    from app.models import User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        return None if u is None else (u.role, u.status, u.org_id, u.email_verified)


def _invite_token(app, to_email):
    blocks = [b for b in get_outbox(app).split("--- ") if f"To: {to_email}" in b]
    assert blocks, f"no email to {to_email}"
    return re.search(r"/invite/([\w-]+)", blocks[-1]).group(1)


def _org_id(app, email):
    return _user(app, email)[2]


# ---------------- public registration is students-only ----------------
@pytest.mark.parametrize("role", ["admin", "examiner", "proctor", "super_admin"])
def test_public_registration_refuses_privileged_roles(client, app, role):
    r = client.post("/register", data=dict(
        name="Mallory", email="mallory@test.com", phone="9000000001", role=role, password="Passw0rd!x"))
    assert r.status_code == 403
    assert _user(app, "mallory@test.com") is None


def test_public_registration_creates_a_student(client, app):
    client.post("/register", data=dict(name="Stu", email="stu@test.com", phone="9000000002", password="Passw0rd!x"))
    assert _user(app, "stu@test.com")[0] == "student"


def test_register_page_offers_no_role_or_create_org_choice(client):
    html = client.get("/register").data.decode()
    assert 'name="role"' not in html and "__new__" not in html and "Register as" not in html
    assert "/request-organization" in html


def test_legacy_new_org_value_cannot_create_an_organization_or_admin(client, app):
    from app.models import Organization
    with app.app_context():
        before = Organization.query.count()
    client.post("/register", data=dict(
        name="Eve", email="eve@test.com", phone="9000000003", org_choice="__new__",
        new_org_name="Evil Corp", password="Passw0rd!x"))
    with app.app_context():
        assert Organization.query.filter_by(name="Evil Corp").first() is None
    u = _user(app, "eve@test.com")
    assert u is None or u[0] == "student"        # the bogus org choice is rejected; never an admin


# ---------------- new organizations need super-admin approval ----------------
def _request_org(client, name="Springfield College", email="head@spring.edu"):
    return client.post("/request-organization", data=dict(
        org_name=name, name="Head", email=email, phone="9000000010", password="Passw0rd!x", accepted_terms="y"),
        follow_redirects=True)


def test_requested_organization_is_pending_and_its_admin_cannot_log_in(client, app):
    from app.models import Organization
    _request_org(client)
    with app.app_context():
        org = Organization.query.filter_by(name="Springfield College").first()
        assert org.status == "pending"
    role, status, _, _ = _user(app, "head@spring.edu")
    assert (role, status) == ("admin", "pending_approval")
    client.post("/verify-email", data={"email": "head@spring.edu", "code": get_last_otp(app)})
    r = login(client, "head@spring.edu", "Passw0rd!x")
    assert b"awaiting approval" in r.data
    assert b"Dashboard" not in r.data


def test_pending_org_is_not_selectable_by_students(client, app):
    _request_org(client)
    assert b"Springfield College" not in client.get("/register").data


def test_super_admin_can_approve_and_then_the_admin_can_log_in(client, app):
    from app.models import Organization
    _request_org(client)
    client.post("/verify-email", data={"email": "head@spring.edu", "code": get_last_otp(app)})
    create_staff(app, "Root", "root@test.com", "9000000099", "super_admin", "Passw0rd!x")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Springfield College").first().id
    sa = app.test_client()
    login(sa, "root@test.com", "Passw0rd!x")
    assert b"Springfield College" in sa.get("/organizations/").data          # shown under pending
    sa.post(f"/organizations/{org_id}/approve")
    with app.app_context():
        assert Organization.query.get(org_id).status == "active"
    client.get("/logout")
    r = login(client, "head@spring.edu", "Passw0rd!x")
    assert b"<title>Admin Dashboard</title>" in r.data


def test_rejected_organization_admin_stays_locked_out(client, app):
    from app.models import Organization
    _request_org(client)
    create_staff(app, "Root", "root@test.com", "9000000099", "super_admin", "Passw0rd!x")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Springfield College").first().id
    sa = app.test_client()
    login(sa, "root@test.com", "Passw0rd!x")
    sa.post(f"/organizations/{org_id}/reject")
    assert _user(app, "head@spring.edu")[1] == "inactive"
    r = login(client, "head@spring.edu", "Passw0rd!x")
    assert b"Dashboard" not in r.data


def test_only_super_admin_can_approve(client, app):
    from app.models import Organization
    _request_org(client)
    create_staff(app, "Adm", "adm@test.com", "9000000050", "admin", "Passw0rd!x")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Springfield College").first().id
    login(client, "adm@test.com", "Passw0rd!x")
    assert client.post(f"/organizations/{org_id}/approve").status_code == 403
    with app.app_context():
        assert Organization.query.get(org_id).status == "pending"


def test_a_pending_organization_cannot_be_activated_by_toggle(client, app):
    from app.models import Organization
    _request_org(client)
    create_staff(app, "Root", "root@test.com", "9000000099", "super_admin", "Passw0rd!x")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Springfield College").first().id
    login(client, "root@test.com", "Passw0rd!x")
    client.post(f"/organizations/{org_id}/toggle-status")
    with app.app_context():
        assert Organization.query.get(org_id).status == "pending"


# ---------------- invitations ----------------
def _admin_client(app, email="boss@test.com", org_name=None):
    create_staff(app, "Boss", email, "9000000060", "admin", "Passw0rd!x", org_name=org_name)
    c = app.test_client()
    login(c, email, "Passw0rd!x")
    return c


def test_admin_can_invite_and_invitee_gets_exactly_that_role_and_org(app):
    boss = _admin_client(app)
    r = boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"}, follow_redirects=True)
    assert b"Invitation sent" in r.data
    token = _invite_token(app, "ex@test.com")

    invitee = app.test_client()
    assert b"Examiner" in invitee.get(f"/invite/{token}").data
    # Hand-edited role/org fields in the form are ignored: the invitation decides.
    invitee.post(f"/invite/{token}", data=dict(
        name="Ex Aminer", phone="9000000061", password="Passw0rd!x", role="admin", org_id="999", accepted_terms="y"))
    role, status, org_id, verified = _user(app, "ex@test.com")
    assert (role, status, verified) == ("examiner", "active", True)
    assert org_id == _org_id(app, "boss@test.com")
    assert b"Passw0rd" not in get_outbox(app).encode()   # no password in mail
    assert login(invitee, "ex@test.com", "Passw0rd!x").status_code == 200


def test_invitation_token_is_stored_only_as_a_hash(app):
    from app.models import StaffInvitation
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "proctor"})
    token = _invite_token(app, "ex@test.com")
    with app.app_context():
        inv = StaffInvitation.query.one()
        assert token not in inv.token_hash and len(inv.token_hash) == 64


def test_invitation_is_single_use(app):
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"})
    token = _invite_token(app, "ex@test.com")
    data = dict(name="Ex", phone="9000000061", password="Passw0rd!x", accepted_terms="y")
    c1, c2 = app.test_client(), app.test_client()
    c1.post(f"/invite/{token}", data=data)
    r = c2.post(f"/invite/{token}", data=dict(data, name="Imposter"), follow_redirects=True)
    assert b"invalid, expired or has already been used" in r.data
    from app.models import User
    with app.app_context():
        assert User.query.filter_by(email="ex@test.com").count() == 1


def test_double_accept_race_creates_one_account(app):
    from app import invitations
    from app.models import StaffInvitation, User
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"})
    with app.app_context():
        inv = StaffInvitation.query.one()
        stale = StaffInvitation.query.get(inv.id)         # a second worker holding the same pending row
        invitations.accept(inv, "A", "9000000061", "Passw0rd!x")
        with pytest.raises(invitations.InvitationError):
            invitations.accept(stale, "B", "9000000062", "Passw0rd!x")
        assert User.query.filter_by(email="ex@test.com").count() == 1


def test_expired_and_revoked_invitations_do_not_work(app):
    from app import db
    from app.models import StaffInvitation
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "old@test.com", "role": "examiner"})
    boss.post("/admin/staff/invite", data={"email": "rev@test.com", "role": "examiner"})
    old, rev = _invite_token(app, "old@test.com"), _invite_token(app, "rev@test.com")
    with app.app_context():
        i = StaffInvitation.query.filter_by(email="old@test.com").one()
        i.expires_at = datetime.utcnow() - timedelta(minutes=1)
        rid = StaffInvitation.query.filter_by(email="rev@test.com").one().id
        db.session.commit()
    boss.post(f"/admin/staff/invitations/{rid}/revoke")
    for tok in (old, rev):
        r = app.test_client().get(f"/invite/{tok}", follow_redirects=True)
        assert b"invalid, expired or has already been used" in r.data
    assert _user(app, "old@test.com") is None and _user(app, "rev@test.com") is None


def test_a_newer_invitation_supersedes_the_older_link(app):
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"})
    first = _invite_token(app, "ex@test.com")
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "proctor"})
    second = _invite_token(app, "ex@test.com")
    assert first != second
    assert b"invalid" in app.test_client().get(f"/invite/{first}", follow_redirects=True).data
    assert b"Proctor" in app.test_client().get(f"/invite/{second}").data


@pytest.mark.parametrize("bad_role", ["super_admin", "student", "root", ""])
def test_cannot_invite_into_non_staff_or_super_admin_roles(app, bad_role):
    from app.models import StaffInvitation
    boss = _admin_client(app)
    boss.post("/admin/staff/invite", data={"email": "x@test.com", "role": bad_role})
    with app.app_context():
        assert StaffInvitation.query.count() == 0


def test_cannot_invite_an_existing_account(app):
    from app.models import StaffInvitation
    boss = _admin_client(app)
    create_staff(app, "Already", "already@test.com", "9000000070", "examiner", "Passw0rd!x")
    boss.post("/admin/staff/invite", data={"email": "already@test.com", "role": "admin"})
    with app.app_context():
        assert StaffInvitation.query.count() == 0


@pytest.mark.parametrize("role", ["examiner", "proctor"])
def test_non_admin_staff_cannot_invite(app, role):
    from app.models import StaffInvitation
    create_staff(app, "Low", "low@test.com", "9000000071", role, "Passw0rd!x")
    c = app.test_client()
    login(c, "low@test.com", "Passw0rd!x")
    assert c.get("/admin/staff").status_code == 403
    assert c.post("/admin/staff/invite", data={"email": "x@test.com", "role": "admin"}).status_code == 403
    with app.app_context():
        assert StaffInvitation.query.count() == 0


def test_students_cannot_reach_staff_management(client, app):
    register_and_verify(client, app, "Stu", "stu@test.com", "9000000072", "student", "Passw0rd!x")
    login(client, "stu@test.com", "Passw0rd!x")
    assert client.get("/admin/staff").status_code == 403


def test_admin_of_another_org_cannot_revoke_or_resend_my_invitations(app):
    from app.models import StaffInvitation
    a = _admin_client(app, "a@test.com", org_name="Org A")
    b = _admin_client(app, "b@test.com", org_name="Org B")
    a.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"})
    with app.app_context():
        inv_id = StaffInvitation.query.one().id
    assert b.post(f"/admin/staff/invitations/{inv_id}/revoke").status_code == 403
    assert b.post(f"/admin/staff/invitations/{inv_id}/resend").status_code == 403
    with app.app_context():
        assert StaffInvitation.query.get(inv_id).status == "pending"
    # And B's staff page doesn't list A's invitation.
    assert b"ex@test.com" not in b.get("/admin/staff").data


def test_invitation_into_the_inviters_own_org_only(app):
    from app.models import StaffInvitation
    a = _admin_client(app, "a@test.com", org_name="Org A")
    a.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner", "org_id": "9999"})
    with app.app_context():
        assert StaffInvitation.query.one().org_id == _org_id(app, "a@test.com")


def test_super_admin_can_invite_an_org_admin(app):
    from app.models import Organization
    create_staff(app, "Root", "root@test.com", "9000000099", "super_admin", "Passw0rd!x")
    create_staff(app, "Seed", "seed@test.com", "9000000098", "admin", "Passw0rd!x", org_name="Org Z")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Org Z").first().id
    sa = app.test_client()
    login(sa, "root@test.com", "Passw0rd!x")
    sa.post(f"/organizations/{org_id}/invite-admin", data={"email": "newadmin@test.com", "role": "admin"})
    app.test_client().post(f"/invite/{_invite_token(app, 'newadmin@test.com')}", data=dict(
        name="New Admin", phone="9000000090", password="Passw0rd!x", accepted_terms="y"))
    assert _user(app, "newadmin@test.com")[:3] == ("admin", "active", org_id)


def test_org_admin_cannot_use_the_super_admin_invite_route(app):
    from app.models import Organization
    boss = _admin_client(app, org_name="Org Q")
    with app.app_context():
        org_id = Organization.query.filter_by(name="Org Q").first().id
    assert boss.post(f"/organizations/{org_id}/invite-admin", data={"email": "x@test.com"}).status_code == 403


def test_invitation_for_an_inactive_org_is_not_usable(app):
    from app import db
    from app.models import Organization
    boss = _admin_client(app, org_name="Org R")
    boss.post("/admin/staff/invite", data={"email": "ex@test.com", "role": "examiner"})
    token = _invite_token(app, "ex@test.com")
    with app.app_context():
        Organization.query.filter_by(name="Org R").first().status = "inactive"
        db.session.commit()
    assert b"invalid" in app.test_client().get(f"/invite/{token}", follow_redirects=True).data


# ---------------- sessions of deactivated accounts ----------------
def test_deactivating_a_user_ends_their_existing_session(app):
    from app import db
    from app.models import User
    create_staff(app, "Ex", "ex@test.com", "9000000080", "examiner", "Passw0rd!x")
    c = app.test_client()
    login(c, "ex@test.com", "Passw0rd!x")
    assert c.get("/admin/tests").status_code == 200
    with app.app_context():
        User.query.filter_by(email="ex@test.com").first().status = "inactive"
        db.session.commit()
    r = c.get("/admin/tests")
    assert r.status_code in (302, 401)           # bounced to login, not served
