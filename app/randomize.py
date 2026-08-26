import json
import random


def build_attempt_order(questions, attempt_token, randomize):
    """Given a list of Question objects, return (question_order, option_order) as
    JSON strings — question_order is a list of question ids in display order,
    option_order maps question id -> list of option keys ('a'..'d') in display
    order. Deterministic per attempt_token so re-rendering the page (refresh,
    resume) doesn't reshuffle mid-exam. Grading always uses the *actual* option
    letter the student picked, so this only changes what's displayed, never how
    answers are scored.
    """
    rng = random.Random(attempt_token)

    question_ids = [q.id for q in questions]
    if randomize:
        rng.shuffle(question_ids)

    option_order = {}
    for q in questions:
        keys = ["a", "b", "c", "d"]
        if randomize:
            rng.shuffle(keys)
        option_order[str(q.id)] = keys

    return json.dumps(question_ids), json.dumps(option_order)


def ordered_questions(questions, question_order_json):
    """Reorder a list of Question objects to match a stored question_order JSON string."""
    if not question_order_json:
        return questions
    order = json.loads(question_order_json)
    by_id = {q.id: q for q in questions}
    return [by_id[qid] for qid in order if qid in by_id]


def get_option_order(option_order_json, question_id):
    if not option_order_json:
        return ["a", "b", "c", "d"]
    order = json.loads(option_order_json)
    return order.get(str(question_id), ["a", "b", "c", "d"])
