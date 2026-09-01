"""Advanced Analytics — pure aggregation functions over data the app already
collects (plus one new signal, per-question time-on-screen — see
Attempt.question_time_spent / Answer.time_spent_seconds). Kept separate from
admin.py's routes, which just call these and render templates, so the
aggregation logic is directly unit-testable without going through the Flask
test client for every case.
"""

from collections import defaultdict
from datetime import datetime, timedelta

UNCATEGORIZED = "Uncategorized"


def _finished_attempts(test):
    """Attempts whose answers are final — an in-progress attempt's answers
    may still change, so none of these stats count it."""
    return [a for a in test.attempts if a.status in ("submitted", "terminated")]


def _earned_marks(answer, question, partial_credit_multi):
    if not answer.selected_option:
        return 0.0
    if question.needs_manual_grading:
        return answer.manual_score or 0.0
    return question.score_for(answer.selected_option, partial_credit_multi)


def question_stats(test):
    """One row per question: how many attempts saw it, how many answered it
    vs. skipped it, a success rate (auto-graded types) or an average-score
    percentage (manually graded types — "correct/incorrect" isn't a yes/no
    for an essay), and average time-on-screen where reported."""
    attempts = _finished_attempts(test)
    total = len(attempts)
    rows = []

    for q in sorted(test.questions, key=lambda q: q.id):
        answers = [a for att in attempts for a in att.answers if a.question_id == q.id]
        answered = [a for a in answers if a.selected_option]
        skipped = total - len(answered)

        row = {
            "question_id": q.id, "question_text": q.question_text,
            "category": q.category or UNCATEGORIZED, "difficulty": q.difficulty,
            "question_type": q.question_type, "marks": q.marks,
            "needs_manual_grading": q.needs_manual_grading,
            "total_attempts": total, "answered_count": len(answered), "skipped_count": skipped,
            "skip_rate": round(100 * skipped / total, 1) if total else None,
        }

        if q.needs_manual_grading:
            graded = [a for a in answered if a.manual_score is not None]
            row["correct_count"] = None
            row["success_rate"] = None
            row["avg_score_pct"] = (
                round(100 * sum(a.manual_score for a in graded) / (len(graded) * q.marks), 1)
                if graded and q.marks else None
            )
        else:
            correct = [a for a in answered if q.is_correct(a.selected_option)]
            row["correct_count"] = len(correct)
            row["success_rate"] = round(100 * len(correct) / len(answered), 1) if answered else None
            row["avg_score_pct"] = row["success_rate"]

        times = [a.time_spent_seconds for a in answers if a.time_spent_seconds]
        row["avg_time_seconds"] = round(sum(times) / len(times), 1) if times else None

        rows.append(row)
    return rows


def question_difficulty(test):
    """Buckets each auto-graded question into easy/medium/hard by *observed*
    success rate — not the admin-set Question.difficulty label — so an
    admin can spot where their guess didn't match how students actually
    did. Manually-graded questions are excluded (no auto-computed success
    rate to bucket by); see avg_score_pct in question_stats for those."""
    rows = [dict(r) for r in question_stats(test) if r["success_rate"] is not None]
    for r in rows:
        if r["success_rate"] >= 70:
            r["observed_difficulty"] = "easy"
        elif r["success_rate"] >= 40:
            r["observed_difficulty"] = "medium"
        else:
            r["observed_difficulty"] = "hard"
        r["label_matches_observed"] = r["difficulty"] == r["observed_difficulty"]
    return sorted(rows, key=lambda r: r["success_rate"])


def most_skipped_questions(test, limit=10):
    rows = [r for r in question_stats(test) if r["skip_rate"]]
    return sorted(rows, key=lambda r: r["skip_rate"], reverse=True)[:limit]


def performance_by_topic(test):
    """Aggregate marks-earned/marks-available by Question.category across
    every finished attempt on this test — how the cohort did per topic."""
    attempts = _finished_attempts(test)
    topic_marks = defaultdict(lambda: {"earned": 0.0, "available": 0.0, "answered": 0, "total": 0})

    for att in attempts:
        for ans in att.answers:
            q = ans.question
            t = topic_marks[q.category or UNCATEGORIZED]
            t["available"] += q.marks
            t["total"] += 1
            if ans.selected_option:
                t["answered"] += 1
            t["earned"] += _earned_marks(ans, q, test.partial_credit_multi)

    rows = [
        {
            "topic": topic, "questions": t["total"], "answered": t["answered"],
            "avg_pct": round(100 * t["earned"] / t["available"], 1) if t["available"] else None,
        }
        for topic, t in topic_marks.items()
    ]
    return sorted(rows, key=lambda r: r["topic"])


def student_topic_performance(student):
    """One student's aggregate performance by topic across every finished
    attempt they've made, on any test — a cross-exam view of their
    strengths/weaknesses by subject area, not scoped to one test."""
    attempts = [a for a in student.attempts if a.status in ("submitted", "terminated")]
    topic_marks = defaultdict(lambda: {"earned": 0.0, "available": 0.0, "answered": 0, "total": 0})

    for att in attempts:
        for ans in att.answers:
            q = ans.question
            t = topic_marks[q.category or UNCATEGORIZED]
            t["available"] += q.marks
            t["total"] += 1
            if ans.selected_option:
                t["answered"] += 1
            t["earned"] += _earned_marks(ans, q, att.test.partial_credit_multi)

    rows = [
        {
            "topic": topic, "questions": t["total"], "answered": t["answered"],
            "avg_pct": round(100 * t["earned"] / t["available"], 1) if t["available"] else None,
        }
        for topic, t in topic_marks.items()
    ]
    return sorted(rows, key=lambda r: r["topic"])


def student_performance_trend(student):
    """This student's finished attempts in chronological order — score,
    score-as-percentage, and violation count over time, for a simple trend
    view of whether they're improving, declining, or picking up more
    proctoring flags across successive exams."""
    attempts = sorted(
        (a for a in student.attempts if a.status in ("submitted", "terminated") and a.score is not None),
        key=lambda a: a.submitted_at or a.started_at,
    )
    rows = []
    for a in attempts:
        total_marks = a.test.total_marks()
        rows.append({
            "attempt_id": a.id, "test_id": a.test_id, "test_title": a.test.title,
            "submitted_at": a.submitted_at or a.started_at, "score": a.score, "total_marks": total_marks,
            "score_pct": round(100 * a.score / total_marks, 1) if total_marks else None,
            "violation_count": a.violation_count, "status": a.status,
        })
    return rows


def weak_areas(test, threshold=50):
    """Actionable "needs attention" list for this test — topics and
    individual questions where the cohort's average is below `threshold`%,
    pulled from performance_by_topic/question_stats and re-sorted
    worst-first. Distinct from question_difficulty (which buckets
    everything into easy/medium/hard for a full picture) — this is
    specifically the subset worth an admin's attention first."""
    topic_rows = [r for r in performance_by_topic(test) if r["avg_pct"] is not None and r["avg_pct"] < threshold]
    question_rows = [
        r for r in question_stats(test)
        if r["avg_score_pct"] is not None and r["avg_score_pct"] < threshold and r["answered_count"] > 0
    ]
    return {
        "threshold": threshold,
        "topics": sorted(topic_rows, key=lambda r: r["avg_pct"]),
        "questions": sorted(question_rows, key=lambda r: r["avg_score_pct"]),
    }


def org_topic_performance(org_id):
    """Same aggregation as performance_by_topic, but across every test in
    the organization rather than one — the cohort baseline
    student_weak_areas compares an individual student's topic performance
    against."""
    from app.models import Test

    tests = Test.query.filter_by(org_id=org_id).all()
    topic_marks = defaultdict(lambda: {"earned": 0.0, "available": 0.0, "answered": 0, "total": 0})

    for test in tests:
        for att in _finished_attempts(test):
            for ans in att.answers:
                q = ans.question
                t = topic_marks[q.category or UNCATEGORIZED]
                t["available"] += q.marks
                t["total"] += 1
                if ans.selected_option:
                    t["answered"] += 1
                t["earned"] += _earned_marks(ans, q, test.partial_credit_multi)

    return {
        topic: (round(100 * t["earned"] / t["available"], 1) if t["available"] else None)
        for topic, t in topic_marks.items()
    }


def student_weak_areas(student, weak_threshold=50, below_cohort_gap=15):
    """This student's topics worth flagging — either below weak_threshold%
    outright, or trailing the org-wide cohort average for that same topic
    (see org_topic_performance) by at least below_cohort_gap points, which
    catches a topic that looks fine in isolation but is actually where
    this student most lags their peers."""
    cohort = org_topic_performance(student.org_id)
    rows = []
    for row in student_topic_performance(student):
        if row["avg_pct"] is None:
            continue
        cohort_avg = cohort.get(row["topic"])
        gap = (cohort_avg - row["avg_pct"]) if cohort_avg is not None else None
        if row["avg_pct"] < weak_threshold or (gap is not None and gap >= below_cohort_gap):
            rows.append({**row, "cohort_avg_pct": cohort_avg, "gap_vs_cohort": round(gap, 1) if gap is not None else None})
    return sorted(rows, key=lambda r: r["avg_pct"])


def time_per_question_analysis(test):
    """Cross-references each question's average time-on-screen against the
    rest of the test and against its own success rate, to tell apart a few
    distinct patterns a bare "avg time" column can't: a question that's
    both slow *and* low-scoring is likely genuinely confusing or poorly
    worded; one that's answered unusually fast *and* low-scoring suggests
    students are misreading or guessing rather than running out of time on
    it; one that's just slow with a fine success rate is probably just a
    longer read, not a problem."""
    rows = [dict(r) for r in question_stats(test) if r["avg_time_seconds"] is not None]
    if not rows:
        return {"rows": [], "avg_time_seconds": None}

    avg_all = sum(r["avg_time_seconds"] for r in rows) / len(rows)
    for r in rows:
        ratio = round(r["avg_time_seconds"] / avg_all, 2) if avg_all else None
        r["time_ratio"] = ratio
        success = r["avg_score_pct"]
        if ratio is not None and success is not None and success < 50 and ratio >= 1.4:
            r["time_flag"] = "slow_and_low_success"
        elif ratio is not None and success is not None and success < 50 and ratio <= 0.6:
            r["time_flag"] = "fast_and_low_success"
        elif ratio is not None and ratio >= 1.4:
            r["time_flag"] = "slow"
        else:
            r["time_flag"] = None

    return {
        "rows": sorted(rows, key=lambda r: r["avg_time_seconds"], reverse=True),
        "avg_time_seconds": round(avg_all, 1),
    }


def org_score_trend(org_id, weeks=12):
    """Average score % of finished attempts across the whole organization,
    bucketed by the ISO week they were submitted in, over the last `weeks`
    weeks — a genuine trend-over-time view of the cohort's performance.
    (exam_comparison above is a snapshot across tests as they stand today,
    not a timeline; this is the timeline.)"""
    from app.models import Test, Attempt

    cutoff = datetime.utcnow() - timedelta(weeks=weeks)
    attempts = (
        Attempt.query.join(Test, Attempt.test_id == Test.id)
        .filter(
            Test.org_id == org_id, Attempt.status.in_(["submitted", "terminated"]),
            Attempt.submitted_at.isnot(None), Attempt.submitted_at >= cutoff, Attempt.score.isnot(None),
        )
        .all()
    )
    buckets = defaultdict(list)
    for a in attempts:
        total_marks = a.test.total_marks()
        if not total_marks:
            continue
        week_key = a.submitted_at.strftime("%G-W%V")  # ISO year-week — stable bucket boundaries regardless of month lengths
        buckets[week_key].append(100 * a.score / total_marks)

    rows = [
        {"week": week, "avg_score_pct": round(sum(pcts) / len(pcts), 1), "attempts": len(pcts)}
        for week, pcts in buckets.items()
    ]
    return sorted(rows, key=lambda r: r["week"])


def exam_comparison(tests):
    """One row per test — attempts, average score %, pass rate, and average
    violation count — for comparing exams side by side."""
    rows = []
    for test in tests:
        attempts = _finished_attempts(test)
        scored = [a for a in attempts if a.score is not None and a.status != "terminated"]
        total_marks = test.total_marks()
        passed = [a for a in scored if a.score >= test.passing_marks]
        rows.append({
            "test_id": test.id, "title": test.title, "test_code": test.test_code,
            "attempts": len(attempts),
            "avg_score_pct": (
                round(100 * sum(a.score for a in scored) / (len(scored) * total_marks), 1)
                if scored and total_marks else None
            ),
            "pass_rate": round(100 * len(passed) / len(scored), 1) if scored else None,
            "avg_violations": round(sum(a.violation_count for a in attempts) / len(attempts), 1) if attempts else None,
        })
    return sorted(rows, key=lambda r: r["title"])


def violation_trends(attempts=None, days=30, org_id=None):
    """Daily violation-event counts over the last `days` days, plus a
    breakdown by event type — across every attempt in the system, scoped
    to a specific list of attempts (e.g. one test's) if `attempts` is
    given, or scoped to one organization's attempts if `org_id` is given
    (attempts and org_id are mutually exclusive uses — pass at most one).
    `attempts=[]` (an empty, non-None list) correctly reports zero
    activity rather than falling back to the unscoped, system-wide query."""
    from app.models import ProctoringEvent, Attempt, Test

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = ProctoringEvent.query.filter(
        ProctoringEvent.severity == "violation", ProctoringEvent.created_at >= cutoff
    )
    if attempts is not None:
        attempt_ids = [a.id for a in attempts]
        if not attempt_ids:
            return {"by_day": _empty_day_series(days), "by_type": [], "total": 0}
        query = query.filter(ProctoringEvent.attempt_id.in_(attempt_ids))
    elif org_id is not None:
        query = query.join(Attempt, ProctoringEvent.attempt_id == Attempt.id).join(
            Test, Attempt.test_id == Test.id
        ).filter(Test.org_id == org_id)

    events = query.all()
    by_day = defaultdict(int)
    by_type = defaultdict(int)
    for e in events:
        by_day[e.created_at.date().isoformat()] += 1
        by_type[e.event_type] += 1

    day_rows = _empty_day_series(days)
    for row in day_rows:
        row["count"] = by_day.get(row["date"], 0)

    type_rows = sorted(
        ({"event_type": t, "count": c} for t, c in by_type.items()),
        key=lambda r: r["count"], reverse=True,
    )
    return {"by_day": day_rows, "by_type": type_rows, "total": len(events)}


def _empty_day_series(days):
    today = datetime.utcnow().date()
    return [{"date": (today - timedelta(days=i)).isoformat(), "count": 0} for i in range(days, -1, -1)]
