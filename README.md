# Exam Proctoring (Python rewrite)

A full rewrite of the original PHP/MySQL [Exam_Proctoring](https://github.com/Santosh02411/Exam_Proctoring)
project as a Python **Flask** web app, with working end-to-end webcam proctoring and a
number of production-readiness features the original never had.

## Stack

- **Flask** — app + routing (blueprints for `auth`, `admin`, `student`, `proctoring`)
- **SQLAlchemy** (Flask-SQLAlchemy) — ORM, defaults to **SQLite** (zero setup); point
  `DATABASE_URL` at MySQL/Postgres instead if you want that
- **Flask-Login** — session auth (replaces PHP's `$_SESSION` handling)
- **Flask-WTF / WTForms** — form rendering, validation, CSRF protection
- **Werkzeug** password hashing (replaces PHP's `password_hash`/`password_verify`)
- **face-api.js** (client-side, in-browser) — real-time face detection *and* identity
  verification during the exam, using the TensorFlow.js model weights (`tiny_face_detector`,
  `face_landmark_68`, `face_recognition`) bundled under `app/static/models/`
- **OpenCV (Haar cascade)** — a *second*, independent, server-side face-count check on
  periodic snapshots, so proctoring doesn't rely solely on the client's browser
- **MediaRecorder API** (browser) — records the webcam+mic feed in 30s chunks, uploaded
  to the server for admin playback
- **Web Audio API** (browser) — periodic RMS volume analysis to flag sustained loud
  audio/talking during the exam
- **itsdangerous** (ships with Flask) — signed, expiring tokens for email verification
  and password reset links
- **Gunicorn** — production WSGI server (see Deployment below)
- **pytest** — automated test suite covering auth, exam flow, and proctoring (`tests/`)

## What it does

**Admin**
- Register/login as admin (email verification + CAPTCHA + rate-limited login)
- Create tests: code, title, description, duration, passing marks, publish window, max
  attempts, negative marking, question/option randomization, and whether students can
  review their answers afterward
- Add / remove questions per test — **single-choice, multiple-choice (2+ correct), or
  short-answer** — one at a time or via **bulk CSV import**
- Edit or delete tests, publish/unpublish, duplicate
- Assign specific students to a test — the picker is **searchable and paginated**, so it
  scales past a handful of students — optionally with **extra time** (accessibility
  accommodation) and an **email notification**
- View all attempts for a test (paginated), per-student score, full proctoring event log,
  **flagged snapshot images**, and **play back the recorded webcam/mic session**
- Browse a system-wide **activity log** of who created/edited/deleted/published/assigned what
- "Manage Tests" clearly distinguishes **your tests** vs **all admins' tests**, with a toggle

**Student**
- Register/login as student — must verify email before logging in; login is rate-limited
  (5 failed attempts locks the account for 15 minutes) and requires a simple CAPTCHA
- Forgot-password flow to reset a lost password via a signed, expiring link
- **One-time face enrollment** (`My Tests → Enroll Face`): capture a reference photo;
  only a 128-value face descriptor is stored, never the photo itself
- See assigned tests, attempt counts, and status (available / in progress / submitted / terminated)
- Start a test behind a consent screen that requests camera+mic access, fullscreen, and
  runs a **live identity check** against the enrolled reference photo before unlocking
  "Start exam" — a test can't be started at all until the student has enrolled
- Take the test with:
  - **question order and per-question option order shuffled** per student (configurable per test)
  - **extended time** automatically applied if the admin granted an accommodation
  - a live countdown timer (auto-submits at zero)
  - continuous in-browser face detection (flags **no face** / **multiple faces**)
  - a periodic snapshot sent to the server for an independent OpenCV face-count check —
    flagged frames are saved for the admin to review
  - **periodic identity re-checks** (~every 25s) against the enrolled reference
  - **audio monitoring** — sustained loud audio/talking flagged via Web Audio RMS analysis
  - **full webcam+mic recording**, uploaded in the background for admin review
  - tab-switch, window-blur, fullscreen-exit and copy/paste detection
  - automatic termination after a configurable number of violations (`MAX_VIOLATIONS`, default 5)
  - **negative marking**, if the admin enabled it for that test
- View pass/fail result, and — if the admin allowed it — a **per-question review** showing
  which answers were right/wrong
- **Retake** a test up to the admin-configured attempt limit

## Project layout

```
exam_proctoring_python/
├── run.py                    # dev entry point (Flask's built-in server)
├── wsgi.py                    # production entry point (for Gunicorn/uWSGI)
├── config.py                   # env-driven configuration
├── requirements.txt
├── Dockerfile / docker-compose.yml / .dockerignore
├── pytest.ini
├── tests/                        # automated test suite (pytest)
│   ├── conftest.py                # fixtures: app/client, register+verify, login helpers
│   ├── test_auth.py                # registration, verification, CAPTCHA, lockout, reset
│   ├── test_exam_flow.py            # test creation, CSV import, retakes, negative marking, review
│   ├── test_proctoring.py            # violations, termination, snapshots, recordings, identity
│   ├── test_question_types.py         # single/multi/short grading, admin validation rules
│   └── test_pagination.py              # assign-students search + pagination
├── app/
│   ├── __init__.py            # app factory, blueprint registration
│   ├── models.py                # User, Test, Question, TestEligibility, Attempt, Answer,
│   │                              # ProctoringEvent, Recording, Snapshot, AdminActivityLog
│   ├── forms.py                   # WTForms
│   ├── auth.py                     # register / login (rate-limited + CAPTCHA) / logout /
│   │                                 # email verification / forgot+reset password
│   ├── admin.py                      # test & question management, CSV import, assignment,
│   │                                   # results, activity log — all paginated
│   ├── student.py                      # dashboard, take test (randomized), submit
│   │                                     # (negative marking), review, retakes
│   ├── proctoring.py                     # /api/proctor/* — events, snapshots (+ storage),
│   │                                       # face enrollment, recording chunks
│   ├── randomize.py                        # deterministic per-attempt question/option shuffling
│   ├── captcha.py                            # simple session-based math CAPTCHA
│   ├── email_utils.py                         # SMTP send + outbox.log/flash fallback, tokens
│   ├── activity_log.py                         # admin audit-trail helper
│   ├── cli.py                                   # `flask init-db` / `flask seed-admin`
│   ├── static/
│   │   ├── css/style.css
│   │   ├── js/proctor.js                          # webcam/mic consent, face-api.js loop,
│   │   │                                            # identity checks, audio RMS, recording, timer
│   │   └── models/                                  # face-api.js model weights (tiny_face_detector,
│   │                                                  # face_landmark_68, face_recognition)
│   └── templates/                                     # Jinja2 templates (auth/, admin/, student/)
```

## Setup (development)

```bash
cd exam_proctoring_python
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # edit SECRET_KEY etc. if you like

export FLASK_APP=run.py            # Windows (cmd): set FLASK_APP=run.py
flask init-db                      # creates the SQLite schema under instance/
flask seed-admin --email admin@example.com --password admin123

python run.py                      # http://localhost:5000
```

Then:
1. Log in as the seeded admin (or register a new admin/student from the login screen).
2. As admin: create a test → add questions (or import a CSV) → assign it to a student → publish it.
3. Log in as that student, enroll your face once, then take the proctored test.

## Running the tests

```bash
pip install -r requirements.txt   # includes pytest
pytest                             # or: pytest -v
```

The suite uses an in-memory SQLite database per test (no `instance/` pollution) and covers:
registration/verification, CAPTCHA + login lockout, password reset, test creation and CSV
import, the full exam-taking flow (including negative marking and randomized order),
retake limits, accessibility extra time, the activity log, proctoring violation/termination
logic, identity-mismatch events, snapshot storage, and recording upload/playback access control.

## Deployment (production)

```bash
docker compose up --build
```

or without Docker:

```bash
pip install -r requirements.txt
export SECRET_KEY=... DATABASE_URL=... MAIL_SERVER=...
flask init-db
gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 wsgi:app
```

Put a real reverse proxy (nginx, Caddy, a cloud load balancer) in front of Gunicorn for
TLS termination. Set `DATABASE_URL` to Postgres/MySQL for anything beyond a single small
deployment — SQLite is fine for development and light use but doesn't handle concurrent
writes well. The `instance/` directory (SQLite file, recordings, snapshots, outbox log)
should be a persistent volume — `docker-compose.yml` already mounts one.

## Notes on the proctoring design

- The browser never uploads raw video for *live analysis* — face-api.js runs the face
  detector and identity check locally and only sends short JSON *event* messages
  (`no_face`, `multiple_faces`, `identity_mismatch`, `audio_violation`, `tab_hidden`,
  `fullscreen_exit`, ...) to `/api/proctor/event`. Separately, the full session **is**
  recorded and uploaded in 30s chunks purely for admin playback after the fact.
- Every ~15s a downscaled JPEG snapshot is sent to `/api/proctor/snapshot`, where the
  Flask server runs its own OpenCV Haar-cascade face count as a second opinion — real
  Python-side image analysis, not just relaying the client's verdict. When that check
  flags an anomaly, the frame itself is saved (`instance/snapshots/<attempt_id>/`) so
  the admin can visually confirm the automated verdict instead of trusting the count alone.
- Violations increment `Attempt.violation_count`; once it reaches `MAX_VIOLATIONS` the
  attempt is auto-terminated server-side (not just in the browser), and every event is
  stored in `ProctoringEvent` for the admin to review per-attempt.

## Identity verification

Students enroll once (`/student/enroll-face`) — face-api.js's `faceRecognitionNet`
computes a 128-value descriptor from a live camera capture and only that descriptor is
sent to and stored by the server (`User.face_descriptor`); the photo itself never leaves
the browser. A proctored test can't be started until enrollment is done. At the consent
screen, and again every ~25s during the exam, the live camera feed's descriptor is
compared to the enrolled one via `faceapi.euclideanDistance()` — a distance above
`FACE_MATCH_THRESHOLD` (default `0.6`, matching face-api.js's own recommendation) logs an
`identity_mismatch` event.

## Session recording

`MediaRecorder` records the webcam+mic stream in 30-second `.webm` chunks throughout the
exam and uploads each one as it's produced, so most of the session survives even if the
tab crashes. Chunks are stored under `instance/recordings/<attempt_id>/` and listed with
inline `<video>` players on the admin's attempt-detail page, gated so only the test's
owning admin can fetch them.

## Email verification, password reset & login security

Verification/reset use `itsdangerous` signed, time-limited tokens — no separate token
table needed. By default (no `MAIL_SERVER` set in `.env`), emails aren't actually sent:
they're appended to `instance/outbox.log` and the link is also flashed directly in the
browser, so the whole flow is testable out of the box. Set `MAIL_SERVER` (and the other
`MAIL_*` vars) to send real email via SMTP instead — no code changes needed. The same
mechanism sends an assignment notification when an admin ticks "email selected students"
while assigning a test.

Login requires a simple session-bound math CAPTCHA and is rate-limited: 5 failed attempts
locks the account for 15 minutes (`User.failed_login_attempts` / `locked_until`), which
resets on a successful login.

## Question types & grading

Each question is `single` (exactly one correct option), `multi` (one or more correct
options — the admin's UI enforces at least one but not all four marked correct), or
`short` (free text). Grading (`Question.is_correct()` in `app/models.py`) is all-or-
nothing per question: for `multi`, the student's selected set must exactly match the
correct set — selecting only some of the correct options, or any incorrect one, scores
zero for that question (no partial credit). `short` is graded case-insensitively with
whitespace trimmed, so "Paris", "paris", and "  Paris  " all match but "Parris" won't —
this is exact-text matching, not semantic grading, so word short-answer questions where
several phrasings could be correct aren't a great fit without a synonym list of your own.
Bulk CSV import supports a `question_type` column (`single`/`multi`/`short`, defaults to
`single`); for `multi`, join the correct letters with `+`, e.g. `a+c`.

## Question randomization & retakes

Set per test: `randomize_questions` shuffles both question order and each question's
option order, deterministically seeded from the attempt's token so a page refresh
mid-exam doesn't reshuffle anything — grading always uses the actual selected option
letter, so shuffled display order never affects scoring. `max_attempts` controls how many
times a student may attempt the test; the dashboard shows attempts used/remaining and
offers "Retake Test" once a previous attempt is graded.

## Differences from the original PHP repo

The original repo only had `login`, `register`, `create_test`, `add_question`,
`edit_test`, and `manage_tests` — it linked to `admin_dashboard.php`,
`student_dashboard.php`, `submit_answers.php`, `assign_students.php`, `view_results.php`,
`view_test.php` and a `/proctor/*.php` recording pipeline that were never included in the
repo, and its bundled face-api.js model directory was missing a required manifest file
(so face detection could never have worked even if finished). This rewrite implements
every missing piece natively in Python, fixes the broken model bundle, and adds a set of
production-readiness features (rate limiting, CAPTCHA, randomization, retakes, negative
marking, accessibility accommodations, bulk import, an audit log, an automated test suite,
and a Docker/Gunicorn deployment path) that the original never had at all.
