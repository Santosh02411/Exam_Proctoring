"""Descriptive/coding answer similarity detection ("plagiarism check").

Compares every pair of submitted free-text answers to the same
descriptive/coding question within a test and flags pairs whose text is
suspiciously alike. This is a text-similarity heuristic, not a proof of
copying — two students can legitimately write similar answers (a short
factual definition, a standard coding idiom), so every flag is meant for
an examiner to look at and confirm/dismiss, never an automated verdict.

Method: difflib.SequenceMatcher.ratio() on normalized text. Descriptive
answers are normalized by collapsing whitespace and lowercasing.
Coding answers get an extra normalization pass that strips comments and
collapses/lowercases identifiers-agnostic whitespace, so two
functionally-identical submissions that only differ in variable names,
indentation, or comments still score as near-identical (which is exactly
the kind of surface-level rewrite plagiarism checks need to see through).
"""

import re
from difflib import SequenceMatcher

from app import db
from app.models import Answer, AnswerSimilarityFlag, Attempt, Question

DEFAULT_THRESHOLD_PCT = 70.0
# Don't bother comparing very short answers — a two-word answer being
# "70% similar" to another two-word answer is noise, not a signal.
MIN_ANSWER_LENGTH = 25

_WHITESPACE_RE = re.compile(r"\s+")
_LINE_COMMENT_RE = re.compile(r"(#|//).*$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/|'''.*?'''|\"\"\".*?\"\"\"", re.DOTALL)


def normalize_text(text):
    """Lowercase + collapse whitespace, for descriptive (essay) answers."""
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def normalize_code(text):
    """Strip comments, then collapse/lowercase whitespace, for coding
    answers. Deliberately simple (no real per-language parsing) — this is
    a similarity heuristic, not a compiler, and over-engineering the
    normalization risks hiding genuine differences as much as surfacing
    superficial ones."""
    code = text or ""
    code = _BLOCK_COMMENT_RE.sub(" ", code)
    code = _LINE_COMMENT_RE.sub("", code)
    return _WHITESPACE_RE.sub(" ", code.strip().lower())


def similarity_pct(text_a, text_b, is_code=False):
    """Similarity between two answers as a 0-100 float."""
    normalize = normalize_code if is_code else normalize_text
    a, b = normalize(text_a), normalize(text_b)
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio() * 100, 1)


def _ordered_pair(answer_a, answer_b):
    """Return (lower_id_answer, higher_id_answer) so a pair is always
    stored in a consistent order — needed for the unique constraint on
    (answer_a_id, answer_b_id) to actually prevent duplicate flags."""
    if answer_a.id <= answer_b.id:
        return answer_a, answer_b
    return answer_b, answer_a


def run_similarity_check(test, threshold_pct=DEFAULT_THRESHOLD_PCT):
    """Compare every pair of submitted descriptive/coding answers within
    `test`, question by question, and create an AnswerSimilarityFlag for
    any pair at or above threshold_pct that isn't already flagged. Only
    answers from different students are compared (a student can't be
    flagged against their own retake). Returns the number of new flags
    created. Safe to re-run — existing flags (including ones an examiner
    already reviewed) are left untouched, and pairs already flagged from
    a prior run are skipped rather than duplicated."""
    questions = Question.query.filter_by(test_id=test.id).filter(
        Question.question_type.in_(("descriptive", "coding"))
    ).all()
    if not questions:
        return 0

    existing_pairs = {
        (f.answer_a_id, f.answer_b_id)
        for f in AnswerSimilarityFlag.query.filter_by(test_id=test.id).all()
    }

    new_flags = 0
    for question in questions:
        is_code = question.question_type == "coding"
        answers = Answer.query.filter_by(question_id=question.id).filter(
            Answer.selected_option.isnot(None)
        ).join(Attempt, Answer.attempt_id == Attempt.id).filter(Attempt.status == "submitted").all()
        # Keep only the most recent submitted answer per student for this
        # question, so a student's own earlier attempt never gets compared.
        by_student = {}
        for answer in answers:
            if len((answer.selected_option or "").strip()) < MIN_ANSWER_LENGTH:
                continue
            student_id = answer.attempt.student_id
            existing = by_student.get(student_id)
            if not existing or answer.attempt.started_at > existing.attempt.started_at:
                by_student[student_id] = answer

        candidates = list(by_student.values())
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                pct = similarity_pct(a.selected_option, b.selected_option, is_code=is_code)
                if pct < threshold_pct:
                    continue
                lo, hi = _ordered_pair(a, b)
                if (lo.id, hi.id) in existing_pairs:
                    continue
                db.session.add(AnswerSimilarityFlag(
                    test_id=test.id, question_id=question.id,
                    answer_a_id=lo.id, answer_b_id=hi.id,
                    answer_type=question.question_type,
                    similarity_pct=pct,
                ))
                existing_pairs.add((lo.id, hi.id))
                new_flags += 1

    if new_flags:
        db.session.commit()
    return new_flags
