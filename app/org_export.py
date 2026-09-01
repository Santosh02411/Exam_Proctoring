"""Organization-level data export — the practical shape "org-level backup"
takes in this app's architecture. The platform backup (app.backup) is a
straight file-copy of the single shared sqlite database, which necessarily
covers every organization at once; there's no way to copy out "just one
org's slice" of a shared file. What an individual organization actually
needs from a "backup" — being able to get their own data out, independent
of the platform operator's backup schedule — is a self-service export of
their own tests, questions, users, and attempt history, which is what this
produces: a JSON document scoped strictly to one org_id, safe for that
org's own admin to download (no password hashes, no other tenant's data).
"""

from datetime import datetime

from app.models import Test, Question, User, Attempt


def export_organization_data(org):
    """Build a JSON-serializable dict of everything belonging to `org` —
    its tests (with questions), its users (roles/status, no credentials),
    and a summary of every attempt. Meant to be downloaded and kept by the
    organization itself, independent of the platform's own backup
    schedule (see app.backup, which is super_admin-only and covers every
    org at once)."""
    tests = Test.query.filter_by(org_id=org.id).order_by(Test.created_at).all()
    users = User.query.filter_by(org_id=org.id).order_by(User.created_at).all()

    def question_dict(q):
        return {
            "id": q.id, "question_type": q.question_type, "question_text": q.question_text,
            "option_a": q.option_a, "option_b": q.option_b, "option_c": q.option_c, "option_d": q.option_d,
            "correct_answer": q.correct_answer, "marks": q.marks, "difficulty": q.difficulty,
        }

    def test_dict(t):
        return {
            "id": t.id, "test_code": t.test_code, "title": t.title, "description": t.description,
            "status": t.status, "duration_minutes": t.duration_minutes, "passing_marks": t.passing_marks,
            "total_questions": t.total_questions, "created_at": t.created_at.isoformat() if t.created_at else None,
            "questions": [question_dict(q) for q in t.questions],
        }

    def user_dict(u):
        return {
            "id": u.id, "user_id": u.user_id, "name": u.name, "email": u.email, "role": u.role,
            "status": u.status, "created_at": u.created_at.isoformat() if u.created_at else None,
        }

    test_ids = [t.id for t in tests]
    attempts = Attempt.query.filter(Attempt.test_id.in_(test_ids)).all() if test_ids else []

    def attempt_dict(a):
        return {
            "id": a.id, "test_id": a.test_id, "student_id": a.student_id, "status": a.status,
            "score": a.score, "violation_count": a.violation_count,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        }

    return {
        "exported_at": datetime.utcnow().isoformat(),
        "organization": {"id": org.id, "name": org.name, "slug": org.slug, "status": org.status},
        "tests": [test_dict(t) for t in tests],
        "users": [user_dict(u) for u in users],
        "attempts": [attempt_dict(a) for a in attempts],
    }
