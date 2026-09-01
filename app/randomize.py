import json
import random


def build_attempt_order(questions, attempt_token, randomize_questions=True, randomize_options=True,
                         pool_size=None):
    """Given a list of Question objects, return (question_order, option_order) as
    JSON strings — question_order is a list of question ids in display order,
    option_order maps question id -> list of option keys ('a'..'d') in display
    order. Deterministic per attempt_token so re-rendering the page (refresh,
    resume) doesn't reshuffle mid-exam. Grading always uses the *actual* option
    letter the student picked, so this only changes what's displayed, never how
    answers are scored. Question-order and option-order shuffling are
    independent switches — a test can shuffle one without the other.

    pool_size, if given and smaller than len(questions), draws that many
    questions at random (per attempt_token) instead of using the full set —
    so different students can end up with different question subsets, not
    just a different order of the same ones. The draw happens before
    randomize_questions shuffles order, so a pooled test with shuffling off
    still gets a random subset, just in original question order. Only
    question ids that made it into the pool get an entry in option_order.
    """
    rng = random.Random(attempt_token)

    question_ids = [q.id for q in questions]
    if pool_size and 0 < pool_size < len(question_ids):
        question_ids = rng.sample(question_ids, pool_size)
        if not randomize_questions:
            # Preserve the original relative order for the drawn subset,
            # rather than the incidental order random.sample() returned it in.
            original_order = {q.id: i for i, q in enumerate(questions)}
            question_ids.sort(key=lambda qid: original_order[qid])
    if randomize_questions:
        rng.shuffle(question_ids)

    pooled_ids = set(question_ids)
    option_order = {}
    for q in questions:
        if q.id not in pooled_ids:
            continue
        keys = ["a", "b", "c", "d"]
        if randomize_options:
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
