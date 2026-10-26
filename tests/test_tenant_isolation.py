"""Multi-tenant authorization audit, enforced by test.

Builds two organizations with realistic data, then — logged in as every kind
of Org-B user (and anonymous) — requests EVERY route in the app that takes an
identifier, using Org A's identifiers. A route passes only if it refuses
(never 2xx), never echoes an Org-A marker string, and leaves every Org-A
database row byte-for-byte unchanged.

Because the route list comes from app.url_map, a NEW route that takes an id
and isn't covered here fails the suite until it is handled (see PARAM_VALUES).
"""
import os
import re

import pytest

from tests.conftest import create_staff, login

MARK = "ZZMARK"          # appears in every Org-A name/title/text; must never reach Org B
PASSWORD = "Passw0rd!x"


# ---------------------------------------------------------------- the world
def build_world(app):
    from datetime import datetime, timedelta
    from app import db
    from app.models import (
        AccommodationRequest, Answer, AnswerSimilarityFlag, ApiKey, Appeal, Attempt, AttemptReview, Certificate,
        Cohort, CohortMembership, IdentityDocument, Organization, ProctorMessage, ProctoringEvent, Question,
        QuestionBankItem, Recording, Section, Snapshot, StaffInvitation, SystemAlert, ErrorLog, Test,
        TestEligibility, User, NotificationLog,
    )
    w = {}

    def staff(name, email, role, org, phone):
        create_staff(app, name, email, phone, role, PASSWORD, org_name=org)

    # --- Org A -----------------------------------------------------------
    staff(f"{MARK}-A-ADMIN", "a-admin@aorg.com", "admin", "ZZMARK-A-ORG", "9100000001")
    staff(f"{MARK}-A-EXAMINER", "a-examiner@aorg.com", "examiner", "ZZMARK-A-ORG", "9100000002")
    staff(f"{MARK}-A-PROCTOR", "a-proctor@aorg.com", "proctor", "ZZMARK-A-ORG", "9100000003")
    staff(f"{MARK}-A-STUDENT-ONE", "a-student1@aorg.com", "student", "ZZMARK-A-ORG", "9100000004")
    staff(f"{MARK}-A-STUDENT-TWO", "a-student2@aorg.com", "student", "ZZMARK-A-ORG", "9100000005")
    staff("Root", "root@platform.com", "super_admin", None, "9100000099")

    with app.app_context():
        org_a = Organization.query.filter_by(name="ZZMARK-A-ORG").first()
        admin = User.query.filter_by(email="a-admin@aorg.com").first()
        s1 = User.query.filter_by(email="a-student1@aorg.com").first()
        s2 = User.query.filter_by(email="a-student2@aorg.com").first()
        w.update(org_a=org_a.id, a_student1=s1.id, a_student2=s2.id, a_admin=admin.id, a_student_email=s1.email)

        test = Test(title=f"{MARK}-A-TEST", test_code="ZZMARKCODE", created_by=admin.id, org_id=org_a.id,
                    status="published", duration_minutes=30, total_questions=1, passing_marks=1)
        db.session.add(test); db.session.flush()
        sec = Section(test_id=test.id, name=f"{MARK}-A-SECTION"); db.session.add(sec); db.session.flush()
        q = Question(test_id=test.id, question_text=f"{MARK}-A-QUESTION", option_a="a", option_b="b", option_c="c",
                     option_d="d", correct_answer="A", marks=1, section_id=sec.id)
        db.session.add(q)
        item = QuestionBankItem(created_by=admin.id, org_id=org_a.id, question_text=f"{MARK}-A-BANK",
                                option_a="a", option_b="b", correct_answer="A")
        db.session.add(item)
        cohort = Cohort(org_id=org_a.id, name=f"{MARK}-A-COHORT"); db.session.add(cohort); db.session.flush()
        db.session.add(CohortMembership(cohort_id=cohort.id, student_id=s1.id))
        db.session.add(TestEligibility(test_id=test.id, student_id=s1.id))
        db.session.add(TestEligibility(test_id=test.id, student_id=s2.id))
        att = Attempt(attempt_token="tok-a-1", test_id=test.id, student_id=s1.id, status="submitted",
                      submitted_at=datetime.utcnow())
        # Student two is MID-EXAM so live-monitor / alert / queue pages have something to leak.
        att2 = Attempt(attempt_token="tok-a-2", test_id=test.id, student_id=s2.id, status="in_progress")
        db.session.add_all([att, att2]); db.session.flush()
        db.session.add(Answer(attempt_id=att.id, question_id=q.id, selected_option="A"))
        db.session.add(Answer(attempt_id=att2.id, question_id=q.id, selected_option="B"))
        db.session.add(ProctoringEvent(attempt_id=att.id, event_type="tab_hidden", severity="violation", details=MARK))
        db.session.add(ProctoringEvent(attempt_id=att2.id, event_type="phone_detected", severity="violation", details=MARK))
        db.session.add(ProctoringEvent(attempt_id=att2.id, event_type="multiple_faces", severity="violation", details=MARK))
        db.session.add(ProctorMessage(attempt_id=att.id, sender_id=admin.id, body=f"{MARK}-MSG"))
        db.session.add(AttemptReview(attempt_id=att.id, decision="cleared", reviewer_id=admin.id, notes=MARK))
        db.session.add(Certificate(attempt_id=att.id, certificate_code="ZZMARKCERT"))
        snap = Snapshot(attempt_id=att.id, filename="s.jpg")
        rec = Recording(attempt_id=att.id, filename="r.webm")
        doc = IdentityDocument(user_id=s1.id, filename="id.jpg")
        db.session.add_all([snap, rec, doc]); db.session.flush()
        appeal = Appeal(attempt_id=att.id, student_id=s1.id, reason=f"{MARK}-APPEAL"); db.session.add(appeal)
        acc = AccommodationRequest(test_id=test.id, student_id=s1.id, requested_extra_minutes=10, reason=f"{MARK}-ACC")
        db.session.add(acc)
        key = ApiKey(org_id=org_a.id, label=f"{MARK}-A-KEY", key_hash="h" * 64, prefix="zzmark")
        db.session.add(key)
        inv = StaffInvitation(org_id=org_a.id, email=f"{MARK.lower()}-inv@aorg.com", role="examiner", token_hash="t" * 64,
                              invited_by_id=admin.id, expires_at=datetime.utcnow() + timedelta(days=1))
        db.session.add(inv)
        db.session.add(NotificationLog(user_id=s1.id, notif_type="exam_scheduled", subject=f"{MARK}-N", body_preview=MARK))
        db.session.flush()
        flag = AnswerSimilarityFlag(test_id=test.id, question_id=q.id, answer_a_id=1, answer_b_id=2,
                                    answer_type="short", similarity_pct=99)
        db.session.add(flag)
        alert = SystemAlert(alert_type="disk", message="x"); err = ErrorLog(error_type="x")
        db.session.add_all([alert, err])
        db.session.commit()

        w.update(test=test.id, question=q.id, section=sec.id, item=item.id, cohort=cohort.id, attempt=att.id,
                 attempt2=att2.id, snapshot=snap.id, recording=rec.id, doc=doc.id, appeal=appeal.id, request=acc.id,
                 key=key.id, invitation=inv.id, flag=flag.id, alert=alert.id, error=err.id, cert="ZZMARKCERT")

        # real files so a vulnerable file-serving route would actually return 200
        for base, sub, name in (
            (app.config["SNAPSHOT_UPLOAD_DIR"], str(att.id), "s.jpg"),
            (app.config["RECORDINGS_DIR"], os.path.join(str(att.id), rec.kind or "webcam"), "r.webm"),
            (app.config["ID_DOCUMENT_UPLOAD_DIR"], str(s1.id), "id.jpg"),
        ):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
            with open(os.path.join(base, sub, name), "wb") as f:
                f.write(b"\xff\xd8secret-org-a-bytes")

        # PKs that belong to Org A *now* (before Org B exists) — what must never change.
        w["a_pks"] = {}
        for t in db.metadata.sorted_tables:
            pk = [c for c in t.primary_key.columns]
            if len(pk) == 1:
                w["a_pks"][t.name] = {r[0] for r in db.session.execute(db.select(pk[0])).fetchall()}

    # --- Org B -----------------------------------------------------------
    staff("YYMARK-B-ADMIN", "b-admin@borg.com", "admin", "YYMARK-B-ORG", "9200000001")
    staff("YYMARK-B-EXAMINER", "b-examiner@borg.com", "examiner", "YYMARK-B-ORG", "9200000002")
    staff("YYMARK-B-PROCTOR", "b-proctor@borg.com", "proctor", "YYMARK-B-ORG", "9200000003")
    staff("YYMARK-B-STUDENT", "b-student@borg.com", "student", "YYMARK-B-ORG", "9200000004")
    with app.app_context():
        w["org_b"] = Organization.query.filter_by(name="YYMARK-B-ORG").first().id
    return w


def fingerprint(app, w):
    from app import db
    out = {}
    with app.app_context():
        for t in db.metadata.sorted_tables:
            pks = w["a_pks"].get(t.name)
            if not pks:
                continue
            pk = list(t.primary_key.columns)[0]
            rows = db.session.execute(t.select().where(pk.in_(pks)).order_by(pk)).fetchall()
            out[t.name] = [tuple(repr(v) for v in r) for r in rows]
    return out


def diff(before, after):
    problems = []
    for table in sorted(set(before) | set(after)):
        b, a = before.get(table, []), after.get(table, [])
        if b != a:
            problems.append(f"{table}: {len(b)} rows before, {len(a)} after; changed={[r for r in a if r not in b][:2]}")
    return problems


# ----------------------------------------------------------- param mapping
def param_values(w):
    return {
        "test_id": w["test"], "question_id": w["question"], "section_id": w["section"], "attempt_id": w["attempt"],
        "item_id": w["item"], "cohort_id": w["cohort"], "student_id": w["a_student1"], "user_id": w["a_student1"],
        "request_id": w["request"], "appeal_id": w["appeal"], "doc_id": w["doc"], "flag_id": w["flag"],
        "key_id": w["key"], "recording_id": w["recording"], "snapshot_id": w["snapshot"], "org_id": w["org_a"],
        "invitation_id": w["invitation"], "alert_id": w["alert"], "error_id": w["error"], "filename": "backup.db",
        "email": w["a_student_email"], "test_code": "ZZMARKCODE", "code": w["cert"], "token": "x" * 20,
        "decision": "grant",
    }


# Routes that are public BY DESIGN even with an id in the path. Each is justified.
PUBLIC_BY_DESIGN = {
    "branding_logo": "org logos are public branding",
    "student.verify_certificate": "certificate verification by unguessable code is the feature",
    "auth.accept_invitation": "token-gated, single use",
    "auth.verify_email_legacy": "redirects only", "auth.reset_password_legacy": "redirects only",
}
# Authenticated by API key (not session) — covered by the dedicated API tests below.
API_KEY_ROUTES = ("api_v1.",)


def _routes(app):
    rules = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        rules.append(r)
    return sorted(rules, key=lambda r: r.rule)


def _fill(rule, values):
    path = rule.rule
    for name in rule.arguments:
        assert name in values, f"{rule.rule}: no test value for <{name}> — add it to param_values() and cover the route"
        path = re.sub(r"<(?:\w+:)?" + name + ">", str(values[name]), path)
    return path


def _actor(app, email):
    c = app.test_client()
    if email:
        login(c, email, PASSWORD)
    return c


def _hit(client, method, path):
    if path.endswith("/stream"):
        return client.open(path, method=method, buffered=False)
    kwargs = {"json": {}} if path.startswith("/api/") and method == "POST" else {"data": {}}
    return client.open(path, method=method, **kwargs)


@pytest.fixture()
def world(app):
    w = build_world(app)
    return w


ACTORS = {
    "anonymous": None,
    "B admin": "b-admin@borg.com",
    "B examiner": "b-examiner@borg.com",
    "B proctor": "b-proctor@borg.com",
    "B student": "b-student@borg.com",
}


def test_positive_control_org_a_admin_can_use_the_ids_so_the_sweep_is_not_vacuous(app, world):
    """Same routes, same ids, but as Org A's own admin: a healthy share must
    succeed. If the ids were wrong/stale every 'denied' result below would be
    meaningless."""
    values, ok, total = param_values(world), 0, 0
    client = _actor(app, "a-admin@aorg.com")
    for rule in _routes(app):
        if not rule.arguments or "GET" not in rule.methods or rule.endpoint.startswith(API_KEY_ROUTES):
            continue
        if rule.endpoint.split(".")[0] in ("organizations", "system_ops") or rule.endpoint in PUBLIC_BY_DESIGN:
            continue
        total += 1
        r = _hit(client, "GET", _fill(rule, values))
        ok += r.status_code == 200
    assert total > 25 and ok / total > 0.6, f"only {ok}/{total} routes worked for the legitimate owner"


@pytest.mark.parametrize("actor", list(ACTORS))
def test_every_route_with_an_id_refuses_other_organizations(app, world, actor):
    values = param_values(world)
    before = fingerprint(app, world)
    failures = []
    for rule in _routes(app):
        if not rule.arguments or rule.endpoint.startswith(API_KEY_ROUTES) or rule.endpoint in PUBLIC_BY_DESIGN:
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            client = _actor(app, ACTORS[actor])              # fresh session per request: no cross-talk
            path = _fill(rule, values)
            r = _hit(client, method, path)
            body = b"" if path.endswith("/stream") else r.data
            if 200 <= r.status_code < 300:
                failures.append(f"{method} {path} ({rule.endpoint}) -> {r.status_code} (should be refused)")
            if MARK.encode() in body:
                failures.append(f"{method} {path} ({rule.endpoint}) -> LEAKED Org-A data (status {r.status_code})")
    failures += [f"Org-A data MODIFIED: {p}" for p in diff(before, fingerprint(app, world))]
    assert not failures, f"{actor}:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("actor", [a for a in ACTORS if a != "anonymous"])
def test_list_pages_without_ids_never_show_other_organizations_data(app, world, actor):
    leaks = []
    client = _actor(app, ACTORS[actor])
    for rule in _routes(app):
        if rule.arguments or "GET" not in rule.methods or rule.rule.endswith("/stream") or rule.rule == "/logout":
            continue
        if rule.endpoint.startswith(API_KEY_ROUTES) or rule.rule.startswith("/ops"):
            continue
        if rule.endpoint == "auth.register":
            # By design the public student sign-up lists active organizations so a
            # student can pick theirs (names only; no staff, tests or people).
            continue
        r = client.get(rule.rule)
        if MARK.encode() in r.data:
            leaks.append(f"GET {rule.rule} ({rule.endpoint}) leaked Org-A data to {actor}")
    assert not leaks, "\n".join(leaks)


@pytest.mark.parametrize("actor", [a for a in ACTORS if a != "anonymous"])
def test_platform_pages_are_super_admin_only(app, world, actor):
    client = _actor(app, ACTORS[actor])
    for rule in _routes(app):
        if rule.endpoint.split(".")[0] not in ("system_ops", "organizations"):
            continue
        path = _fill(rule, param_values(world))
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            r = _hit(client, method, path)
            assert not (200 <= r.status_code < 300), f"{actor} reached {method} {path}"
            assert r.status_code in (301, 302, 303, 400, 401, 403, 404, 405), (method, path, r.status_code)


def test_super_admin_impersonating_org_b_cannot_reach_org_a(app, world):
    values = param_values(world)
    before = fingerprint(app, world)
    problems = []
    for rule in _routes(app):
        if not rule.arguments or rule.endpoint.startswith(API_KEY_ROUTES) or rule.endpoint in PUBLIC_BY_DESIGN:
            continue
        if rule.endpoint.split(".")[0] in ("organizations", "system_ops"):
            continue
        client = _actor(app, "root@platform.com")
        with client.session_transaction() as s:
            s["impersonate_org_id"] = world["org_b"]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            path = _fill(rule, values)
            r = _hit(client, method, path)
            body = b"" if path.endswith("/stream") else r.data
            if 200 <= r.status_code < 300 or MARK.encode() in body:
                problems.append(f"{method} {path} -> {r.status_code}")
    problems += diff(before, fingerprint(app, world))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path_tpl", [
    "/student/attempts/{attempt}/result", "/student/attempts/{attempt}/review", "/student/attempts/{attempt}/certificate",
    "/student/attempts/{attempt}/heartbeat", "/student/attempts/{attempt}/appeal",
])
def test_a_student_cannot_open_another_students_attempt_even_in_the_same_org(app, world, path_tpl):
    c = _actor(app, "a-student2@aorg.com")             # student TWO asks for student ONE's attempt
    r = c.get(path_tpl.format(**world))
    assert not (200 <= r.status_code < 300), r.status_code
    assert b"ZZMARK-A-STUDENT-ONE" not in r.data


def test_same_org_student_cannot_read_anothers_id_document_or_recording(app, world):
    c = _actor(app, "a-student2@aorg.com")
    for path in (f"/api/proctor/id-document/image/{world['doc']}", f"/api/proctor/recordings/{world['recording']}/file",
                 f"/api/proctor/snapshots/{world['snapshot']}/file"):
        assert not (200 <= c.get(path).status_code < 300), path


def test_owner_can_still_read_their_own_id_document(app, world):
    c = _actor(app, "a-student1@aorg.com")
    assert c.get(f"/api/proctor/id-document/image/{world['doc']}").status_code == 200


def test_same_org_admin_can_review_id_documents_but_another_orgs_admin_cannot(app, world):
    assert _actor(app, "a-admin@aorg.com").get(f"/api/proctor/id-document/image/{world['doc']}").status_code == 200
    assert _actor(app, "b-admin@borg.com").get(f"/api/proctor/id-document/image/{world['doc']}").status_code in (403, 404)


# ------------------------------------------------ ids smuggled in the body
@pytest.mark.parametrize("path,payload", [
    ("/api/proctor/event", {"event_type": "tab_hidden", "severity": "violation"}),
    ("/api/proctor/snapshot", {"image": "data:image/jpeg;base64,/9j/4AAQ"}),
    ("/api/proctor/report-issue", {"description": "x"}),
    ("/api/proctor/geo-check", {"latitude": 1, "longitude": 1}),
    ("/api/proctor/id-document/confirm-match", {}),
])
def test_proctor_apis_reject_an_attempt_id_that_belongs_to_someone_else(app, world, path, payload):
    from app import db
    from app.models import ProctoringEvent
    c = _actor(app, "b-student@borg.com")
    with app.app_context():
        before = ProctoringEvent.query.filter_by(attempt_id=world["attempt"]).count()
    r = c.post(path, json=dict(payload, attempt_id=world["attempt"]))
    assert not (200 <= r.status_code < 300 and (r.get_json(silent=True) or {}).get("ok", True)), (path, r.status_code, r.data[:200])
    with app.app_context():
        assert ProctoringEvent.query.filter_by(attempt_id=world["attempt"]).count() == before


def test_recording_chunk_upload_to_someone_elses_attempt_is_rejected(app, world):
    import io
    from app.models import Recording
    c = _actor(app, "b-student@borg.com")
    with app.app_context():
        before = Recording.query.filter_by(attempt_id=world["attempt"]).count()
    r = c.post("/api/proctor/recording/chunk", data={
        "attempt_id": str(world["attempt"]), "chunk_index": "0", "kind": "webcam",
        "chunk": (io.BytesIO(b"x" * 100), "c.webm")}, content_type="multipart/form-data")
    assert not (200 <= r.status_code < 300)
    with app.app_context():
        assert Recording.query.filter_by(attempt_id=world["attempt"]).count() == before


def test_autosave_and_submit_on_someone_elses_attempt_are_rejected(app, world):
    c = _actor(app, "b-student@borg.com")
    for path in (f"/student/attempts/{world['attempt']}/autosave", f"/student/attempts/{world['attempt']}/submit"):
        assert not (200 <= c.post(path, json={"answers": {}}).status_code < 300)


# ------------------------------------------------ ids smuggled in FORM bodies
def test_admin_forms_cannot_pull_other_orgs_students_cohorts_or_bank_items_into_my_org(app, world):
    """Assign/cohort/bank routes take ids in the POST body: org B's admin may
    not enroll Org A's students in B's test, add them to B's cohort, or copy A's bank."""
    from app import db
    from app.models import Cohort, CohortMembership, Question, Test, TestEligibility, User
    b = _actor(app, "b-admin@borg.com")
    b.post("/admin/tests/create", data=dict(
        test_code="BT1", title="YYMARK B test", description="d", duration_minutes=10, total_questions=1,
        passing_marks=1, status="published", max_attempts=1, negative_marks_per_wrong=0))
    b.post("/admin/cohorts/create", data={"name": "YYMARK B cohort"})
    with app.app_context():
        bt = Test.query.filter_by(test_code="BT1").first()
        bc = Cohort.query.filter_by(name="YYMARK B cohort").first()
        assert bt and bc, "setup failed: B's own objects were not created"
        bt_id, bc_id = bt.id, bc.id
    victims = [world["a_student1"], world["a_student2"]]
    b.post(f"/admin/tests/{bt_id}/assign", data={"student_ids": [str(v) for v in victims]})
    b.post(f"/admin/cohorts/{bc_id}/add-student", data={"student_ids": [str(v) for v in victims]})
    b.post(f"/admin/tests/{bt_id}/assign-cohort", data={"cohort_id": str(world["cohort"])})
    b.post(f"/admin/tests/{bt_id}/questions/from-bank", data={"item_ids": [str(world["item"])]})
    b.post(f"/admin/tests/{bt_id}/eligibility/{victims[0]}/update", data={"extra_minutes": "99"})
    with app.app_context():
        assert TestEligibility.query.filter_by(test_id=bt_id).filter(TestEligibility.student_id.in_(victims)).count() == 0
        assert CohortMembership.query.filter_by(cohort_id=bc_id).count() == 0
        assert not Question.query.filter_by(test_id=bt_id).filter(Question.question_text.like(f"{MARK}%")).count()


def test_junk_ids_in_forms_do_not_crash_the_server(app, world):
    a = _actor(app, "a-admin@aorg.com")
    for path, data in ((f"/admin/tests/{world['test']}/assign", {"student_ids": ["abc", ""]}),
                       (f"/admin/cohorts/{world['cohort']}/add-student", {"student_ids": ["x"]}),
                       (f"/admin/tests/{world['test']}/questions/from-bank", {"item_ids": ["zzz"]}),
                       (f"/admin/tests/{world['test']}/sections/reorder", {"section_id": ["q"]})):
        assert a.post(path, data=data).status_code < 500, path


def test_assigning_proctor_from_another_org_is_ignored(app, world):
    from app import db
    from app.models import Attempt, User
    # A's admin tries to assign B's proctor to A's attempt.
    a = _actor(app, "a-admin@aorg.com")
    with app.app_context():
        b_proctor = User.query.filter_by(email="b-proctor@borg.com").first().id
    a.post(f"/admin/attempts/{world['attempt']}/assign-proctor", data={"proctor_id": str(b_proctor)})
    with app.app_context():
        assert Attempt.query.get(world["attempt"]).assigned_proctor_id != b_proctor


def test_import_users_never_creates_privileged_accounts(app, world):
    import io
    from app.models import User
    a = _actor(app, "a-admin@aorg.com")
    csv_data = "name,email,phone,role,org_id\nSneaky,sneaky@aorg.com,9000000000,admin,%d\n" % world["org_b"]
    a.post("/admin/users/import", data={"csv_file": (io.BytesIO(csv_data.encode()), "u.csv")},
           content_type="multipart/form-data")
    with app.app_context():
        u = User.query.filter_by(email="sneaky@aorg.com").first()
        assert u is None or (u.role == "student" and u.org_id == world["org_a"])


# ------------------------------------------------ API-key (LMS) routes
def _make_key(app, org_id, label):
    from app import db
    from app.models import ApiKey
    import app.api_v1 as api
    raw, prefix, key_hash = api.generate_api_key()      # the app's own salted-hash scheme
    with app.app_context():
        db.session.add(ApiKey(org_id=org_id, label=label, key_hash=key_hash, prefix=prefix))
        db.session.commit()
    return raw


def test_api_key_of_org_b_cannot_see_or_enroll_into_org_a(app, world):
    raw = _make_key(app, world["org_b"], "B-key")
    c = app.test_client()
    h = {"Authorization": f"Bearer {raw}"}
    listing = c.get("/api/v1/tests", headers=h)
    assert listing.status_code == 200 and MARK.encode() not in listing.data
    for method, path in (("GET", "/api/v1/tests/ZZMARKCODE"), ("GET", "/api/v1/tests/ZZMARKCODE/results"),
                         ("POST", "/api/v1/tests/ZZMARKCODE/enroll"),
                         ("GET", f"/api/v1/students/{world['a_student_email']}/attempts")):
        r = c.open(path, method=method, headers=h, json={"emails": [world["a_student_email"]]})
        body = r.get_json(silent=True)
        assert r.status_code in (403, 404) or not body or MARK not in str(body), (path, r.status_code, body)
        assert MARK.encode() not in r.data, path


def test_live_alert_feed_is_scoped_to_the_callers_organization(app, world):
    from app.proctoring import get_live_alerts_since
    with app.app_context():
        mine = get_live_alerts_since(0, world["org_a"])
        theirs = get_live_alerts_since(0, world["org_b"])
    assert mine, "setup: org A should have high-severity alerts to see"
    assert theirs == []


def test_live_monitor_json_for_org_b_excludes_org_a_in_progress_attempts(app, world):
    for actor in ("b-admin@borg.com", "b-proctor@borg.com", "b-examiner@borg.com"):
        c = _actor(app, actor)
        for path in ("/admin/live", "/admin/live/data", "/admin/review-queue", "/admin/appeals"):
            r = c.get(path)
            assert MARK.encode() not in r.data, (actor, path)
