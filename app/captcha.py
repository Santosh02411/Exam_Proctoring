import random

from flask import session


def generate_captcha():
    """Create a simple arithmetic CAPTCHA, store the answer in the session
    (server-side, never sent to the client), and return the question text."""
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    op = random.choice(["+", "-"])
    if op == "-" and b > a:
        a, b = b, a
    answer = a + b if op == "+" else a - b
    session["captcha_answer"] = answer
    return f"{a} {op} {b} = ?"


def verify_captcha(user_answer):
    expected = session.pop("captcha_answer", None)
    if expected is None:
        return False
    try:
        return int(str(user_answer).strip()) == expected
    except (ValueError, TypeError):
        return False
