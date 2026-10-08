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
- **reportlab** — server-side PDF generation (organization report exports)
- **pytest** — automated test suite covering auth, exam flow, proctoring, multi-tenancy,
  and operations (`tests/`)

## What it does

**Roles**: `student` → `examiner`/`proctor` → `admin` (an organization's own admin,
scoped to that org — see Multi-tenancy below) → `super_admin` (platform operator, not
tied to any organization). Registration lets you join an existing organization or —
admin role only — create a new one, bootstrapping that org's first admin; `super_admin`
is never self-serve, only created via `flask create-super-admin`.

**Admin** (scoped to their own organization)
- Register/login as admin (email verification + CAPTCHA + rate-limited login)
- Create tests: code, title, description, duration, passing marks, publish window, max
  attempts, negative marking, question/option randomization, an optional **question
  pool** (draw N random questions per student out of a larger set), and whether students
  can review their answers afterward
- Add / remove questions per test — **single-choice, multiple-choice (2+ correct),
  short-answer, descriptive, or coding** — one at a time, via **bulk CSV import**, or
  pulled from a shared **question bank**
- Edit or delete tests, publish/unpublish, duplicate
- Assign specific students to a test — the picker is **searchable and paginated**, so it
  scales past a handful of students — optionally with **extra time** (accessibility
  accommodation) and an **email notification**
- View all attempts for a test (paginated), per-student score, full proctoring event log,
  **flagged snapshot images**, **play back the recorded webcam/mic session**, and a
  suspicion score with a **plain-language explanation of why it was flagged** (see AI
  Proctoring Signals below) — not just a bare number
- Run a **plagiarism / answer-similarity check** on descriptive and coding answers —
  flags suspiciously similar submissions between students for review, confirm, or dismiss
- Browse an **activity log**, a **security log** (login sessions, device/IP, anomaly
  detection), and an **ID-verification queue**, all scoped to their own organization
- View and export an **organization-level report** (CSV/PDF) — user counts, test/attempt
  volume, pass rate
- Override their organization's own **data retention windows**, and pull a self-service
  **data export** of their org's tests/users/attempts
- "Manage Tests" clearly distinguishes **your tests** vs **all of your org's admins' tests**

**Super Admin** (platform operator — see Multi-tenancy & System Monitoring below)
- Create/activate/deactivate organizations; view a per-org usage report (with CSV/PDF
  export) and each org's admins/recent tests
- **Read-only "view as" support access** into a specific organization's exam content
  (tests, questions, results, analytics, proctor queue) — GET-only, so nothing can be
  created, edited, or deleted while impersonating; user management, security settings,
  and retention policy stay off-limits regardless
- A **system health dashboard** (uptime, DB connectivity, disk/memory, platform-wide
  counts), platform-wide **active sessions**, **storage usage** breakdown, **failed
  proctoring sessions**, **recording-storage management** (bulk cleanup by age), an
  **error log**, **database backups**, and **data retention** policy (platform default +
  per-org overrides)
- **Real-time health alerts** — disk usage and error-rate spikes notify every super_admin
  by email (and Slack, if configured) the moment they cross threshold, not just when
  someone happens to check the dashboard

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
  - **question order and per-question option order shuffled** per student, and — if the
    test uses a **question pool** — a random subset of questions too, so two students
    can get genuinely different question sets, not just a different order
  - **extended time** automatically applied if the admin granted an accommodation
  - a live countdown timer (auto-submits at zero)
  - continuous in-browser face detection (flags **no face** / **multiple faces**)
  - a periodic snapshot sent to the server for an independent OpenCV face-count check —
    flagged frames are saved for the admin to review
  - **advanced head-pose detection** — yaw *and* pitch (left/right/up/down), both a
    sustained-turn check and a repeated-excursion pattern check (see AI Proctoring
    Signals below)
  - **AI gaze tracking** — eye/iris position estimation, independent of head pose
  - **periodic identity re-checks** (~every 25s) against the enrolled reference
  - **audio monitoring** — sustained loud audio/talking flagged via Web Audio RMS analysis
  - **full webcam+mic recording**, uploaded in the background for admin review
  - tab-switch, window-blur, fullscreen-exit and copy/paste detection
  - automatic termination after a configurable number of violations (`MAX_VIOLATIONS`, default 5)
  - **negative marking**, if the admin enabled it for that test
  - at most **one active login session** at a time — signing in elsewhere logs the other
    session out on its next request (see Exam Integrity Controls below)
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
│   ├── test_question_types.py         # single/multi/short grading, partial credit, admin validation
│   ├── test_pagination.py              # assign-students search + pagination
│   └── test_time_limits.py              # per-question time limit persistence, CSV, rendering
├── .github/workflows/tests.yml        # CI: pytest matrix + compileall sanity check
├── app/
│   ├── __init__.py            # app factory, blueprint registration, error-signal wiring,
│   │                            # org-branding context processor
│   ├── models.py                # User, Organization, Test, Question, QuestionBankItem,
│   │                              # TestEligibility, Attempt, Answer, ProctoringEvent,
│   │                              # Recording, Snapshot, AdminActivityLog, AnswerSimilarityFlag,
│   │                              # LoginSession, LoginSecurityEvent, ErrorLog, SystemAlert,
│   │                              # RetentionPolicy
│   ├── forms.py                   # WTForms
│   ├── auth.py                     # register (+ org join/create + ToS consent) / login /
│   │                                 # logout / email verification / forgot+reset password
│   ├── legal.py                     # public /terms and /privacy pages
│   ├── branding.py                   # org-level logo/color, applied post-login via a
│   │                                   # context processor
│   ├── admin.py                      # test & question management, CSV import, assignment,
│   │                                   # results, question bank, plagiarism review, user
│   │                                   # management, analytics, org-scoped retention/report/
│   │                                   # backup — all org-scoped, all paginated
│   ├── student.py                      # dashboard, take test (randomized + pooled),
│   │                                     # submit (negative marking), review, retakes
│   ├── proctoring.py                     # /api/proctor/* — events, snapshots (+ storage),
│   │                                       # face enrollment, recording chunks
│   ├── organizations.py                    # multi-tenant platform layer (super_admin only):
│   │                                         # create/manage orgs, org reports, impersonation
│   ├── system_ops.py                         # System Monitoring & Operations (super_admin
│   │                                           # only): health, sessions, storage, failed
│   │                                           # sessions, recordings, errors, backups, retention
│   ├── similarity.py                          # plagiarism/answer-similarity detection
│   ├── security.py                             # session/device/IP tracking, single-session
│   │                                             # enforcement, login anomaly detection
│   ├── alerting.py                              # real-time disk-usage/error-rate health alerts
│   ├── error_monitoring.py                       # unhandled-exception logging (feeds alerting)
│   ├── backup.py                                  # sqlite file-copy + pg_dump/mysqldump
│   │                                                # platform backups
│   ├── retention.py                                # data retention policy resolution + sweep
│   ├── org_export.py                                # per-org self-service JSON data export
│   ├── org_reports.py                                # shared org-level report + CSV/PDF renderers
│   ├── randomize.py                                   # deterministic per-attempt question/
│   │                                                    # option shuffling + pool selection
│   ├── captcha.py                                      # simple session-based math CAPTCHA
│   ├── email_utils.py                                   # SMTP send + outbox.log/flash fallback, tokens
│   ├── activity_log.py                                   # admin audit-trail helper
│   ├── cli.py                                             # init-db / seed-admin / create-super-admin /
│   │                                                        # backup-db / apply-retention /
│   │                                                        # check-health-alerts / send-reminders
│   ├── static/
│   │   ├── css/style.css
│   │   ├── js/proctor.js                          # webcam/mic consent, face-api.js loop,
│   │   │                                            # identity checks, audio RMS, recording, timer
│   │   └── models/                                  # face-api.js model weights (tiny_face_detector,
│   │                                                  # face_landmark_68, face_recognition)
│   └── templates/                                     # Jinja2 templates (auth/, admin/, student/,
│                                                         # organizations/, system_ops/)
```

## Upgrading an existing database

`db.create_all()` (run at every startup) only creates whole tables that don't exist yet —
on a database from a previous version of this app, it silently leaves every table that
*does* already exist completely untouched. If a newer version of the code has added a
column to one of those tables, the very first query that touches it fails with
`OperationalError: no such column: ...`, no matter how long ago that column was added or
how many have piled up since.

`app/__init__.py` closes that gap with `_ensure_columns`, which runs right after
`db.create_all()` on every startup: it walks every table the models define, compares each
one's columns against what the live database actually has (via `sqlalchemy.inspect`), and
issues a plain `ALTER TABLE ... ADD COLUMN` for anything missing — the full set, not a
specific list, so upgrading never depends on anyone having remembered to add an entry for
a particular feature. It's a no-op (and safe to run on every boot) once the database is
current, which is also exactly what happens on a brand-new install, where `create_all()`
already created every column up front.

This is deliberately a reconciliation step, not a real migration framework — it only ever
*adds* missing columns, never renames, drops, or changes a column's type, and a column
whose model default can't be expressed as plain SQL (for example `datetime.utcnow`, a
Python callable) is added as nullable rather than guessing a value for existing rows. If
you're running this app and `flask` commands or pages start throwing `no such column`
errors after pulling a newer version, that's this step doing its job on the next startup —
just run the app (or any `flask` CLI command) once and retry.

## Setup (development)

```bash
cd exam_proctoring_python
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # edit SECRET_KEY etc. if you like

export FLASK_APP=run.py            # Windows (cmd): set FLASK_APP=run.py
flask init-db                      # creates the SQLite schema under instance/
flask seed-admin --email admin@example.com --password 'Admin123!'
# Optional: a platform-level operator account for Organizations + Operations
# (System Monitoring & Operations) — see the Multi-tenancy section below.
flask create-super-admin --email platform@example.com --password 'Platform123!'

python run.py                      # http://localhost:5000
```

Then:
1. Log in as the seeded admin (or register a new admin/student from the login screen).
2. As admin: create a test → add questions (or import a CSV) → assign it to a student → publish it.
3. Log in as that student, enroll your face once, then take the proctored test.
4. Optionally, log in as the super_admin to see the Organizations and Operations areas.

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

## AI Proctoring Signals

Three specific signals, in `app/static/js/proctor.js` (client-side detection) and
`app/proctoring.py` (server-side scoring/explanation):

- **Advanced head-pose detection** — `estimateYawRatio`/`estimatePitchRatio` extend the
  existing single-axis "sustained turn" check (`looking_away`) with a vertical (up/down)
  estimate too, both from the same 68-point face-landmarks already loaded for identity
  verification (nose-tip position relative to the eye line, normalized by face
  width/height — a coarse geometric proxy, not a calibrated 3D pose solver). On top of
  that, `classifyHeadDirection`/`recordHeadDirectionSample` track a rolling history of
  distinct look-away *excursions* (transitions from center into an off-axis direction) and
  flag a new `repeated_head_movement` event when several land within a short window —
  reading differently from one sustained turn (which could just be someone stretching) and
  from occasional normal glances.
- **AI gaze tracking** — deliberately independent of head pose: someone can hold their head
  still and only move their eyes. Standard webcams and face-api.js's 68 landmarks don't
  give true iris landmarks, so `estimateGazeOffsetForEye` uses a classic lightweight
  technique ("dark pupil" tracking): crop each eye using its existing contour landmarks,
  find the darkest cluster of pixels within the crop via canvas pixel analysis (the
  iris/pupil is reliably darker than the surrounding sclera/skin), and use that cluster's
  offset from the eye's center as a gaze-direction estimate. A sustained off-center offset
  reports a `gaze_away` event. This is an honest heuristic, not biometric-grade eye
  tracking — it's sized to catch someone reading off-screen material for several seconds,
  not to build a precise gaze heatmap.
- **AI suspicion explanation** — `compute_suspicion_score()` already produced a 0–100 score
  plus pattern-level "signals" (burst, escalating trend, violation diversity); it now also
  returns `reasons`: one plain-language line per distinct violation type that actually
  occurred on the attempt (e.g. "The eyes drifted away from the screen for a sustained
  period — flagged 3 times"), ordered most-serious-first by the same per-event weight used
  in the score itself. Shown as a "Why this attempt was flagged" panel on the attempt
  detail page, and as a one-line top reason per row in the review queue. This is a
  deterministic, rule-based explanation over the actual event log — not an LLM-generated
  summary — which is deliberate for a proctoring context: every line traces back to a
  specific, auditable event, not to an opaque model's paraphrase of one.

## Identity verification

Students enroll once (`/student/enroll-face`) — face-api.js's `faceRecognitionNet`
computes a 128-value descriptor from a live camera capture and only that descriptor is
sent to and stored by the server (`User.face_descriptor`); the photo itself never leaves
the browser. A proctored test can't be started until enrollment is done. At the consent
screen, and again every ~25s during the exam, the live camera feed's descriptor is
compared to the enrolled one via `faceapi.euclideanDistance()` — a distance above
`FACE_MATCH_THRESHOLD` (default `0.6`, matching face-api.js's own recommendation) logs an
`identity_mismatch` event.

This verifies "the person at the camera is the same one who enrolled," not "this person is
who they claim to be" — there's no check against a government-issued ID, so it can't catch
someone who has someone else enroll on their behalf in the first place. See
[Known limitations](#known-limitations-by-design) below.

## Session recording

`MediaRecorder` records the webcam+mic stream in 30-second `.webm` chunks throughout the
exam and uploads each one as it's produced, so most of the session survives even if the
tab crashes. Chunks are stored under `instance/recordings/<attempt_id>/` and listed with
inline `<video>` players on the admin's attempt-detail page, gated so only the test's
owning admin can fetch them.

## Real (viewer-local) time, scheduling emails, and automatic reminders

**All notifications go to the email a student registered with** (`User.email`, set at
registration and not editable from the profile page) — this was already true throughout
the app (see `notify()` in `app/notifications.py`) and hasn't changed; noted here because
it's easy to assume otherwise.

**Timestamps now show the viewer's real local time, not a raw UTC number.** Every
timestamp in this app is stored and entered as naive UTC — that was already consistent,
but several pages (the calendar, the review queue, the attempt detail page, appeals,
notification history) rendered it with a plain `strftime` and no timezone label. To
someone outside UTC that reads as flat-out wrong: a test set for "22:00" looks like 10pm
local when it's actually 10pm UTC. The fix is `local_time()` (macro in
`_components.html`) plus `applyLocalTimes()` (script in `base.html`): every timestamp is
rendered as `<time class="localtime" data-utc="...">` with an explicit, correct "...
UTC" fallback (right even with JS off or before the page script runs), and a small
script rewrites it, once, to the viewer's actual real local time and date via the
browser's own `Intl`/`toLocaleString` — no server-side timezone or locale configuration
needed. Applied to the student and admin calendars, the student dashboard (which now also
shows each test's Opens/Closes window), the review queue, the attempt detail page, appeals
(both sides), and notification history. Admin-facing per-event technical logs (the
Behavior Timeline, Proctoring Log, session/device detail) are left in plain UTC and
labeled as such — those correlate with recording timestamps and an admin's own clock
reference, where converting would do more harm than good. Test scheduling input fields
(start/end time) are still entered and interpreted as UTC, same as always; the "Opens"/
"Closes" preview on the student dashboard double-checks that for an admin.

**Scheduling emails now include the date, time, and the test's rules.** "You've been
assigned a new test" and the starting-soon reminder previously stated only a duration and
a raw start timestamp. Both now also include the passing marks, attempts allowed, the
test's open/close window as an explicit date plus a UTC time, and — if the admin set
any — the test's **Instructions for students** field (the same "rules" shown on the
pre-exam screen) under "Rules & instructions for this exam," so a student has the actual
exam rules in their inbox, not just a login link.

**The 1-hour-before reminder now actually fires on its own.** `send_starting_soon_reminders()`
(dedup'd per student/test via `NotificationLog`, so it's safe to call repeatedly) always
worked correctly once triggered, but previously the *only* triggers were an admin clicking
"Send Starting-Soon Reminders Now" on the Notifications page, or an external cron/Task
Scheduler entry calling `flask send-reminders` — nothing in the app itself ever called it.
On a deployment without that external cron set up, reminders simply never went out.
`app/scheduler.py` closes that gap with a small, dependency-free background thread
(no APScheduler/Celery) started from `create_app`, which re-runs the sweep every
`REMINDER_SCHEDULER_INTERVAL_SECONDS` (default 300s). It skips itself under `TESTING`,
under Flask's debug-mode reloader's watcher half (so it doesn't start twice), and can be
turned off with `ENABLE_BACKGROUND_SCHEDULER=false` to rely solely on the manual button or
an external cron instead — the dedup means nothing is ever double-sent either way, however
it's triggered.

**The pre-exam rules now state the actual configured warning limit.** The "you must stay
in fullscreen..." paragraph shown before a student starts a proctored exam now also says,
in plain language, how many warnings this specific test allows before the next one
disqualifies them (see "Warning-based disqualification" above) — reflecting
`Test.max_warnings`/the platform default, not a generic "after repeated warnings."

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

Registration, `/forgot-password`, and `/resend-verification` are separately throttled
**per IP address** (`IpRateLimit` model): 5 requests per hour for registration, 5 per 15
minutes for the other two, by default. Every request counts against the window whether
it succeeds or not, so scripted spam can't dodge the limit by submitting invalid data.
Limits are tunable via `REGISTER_MAX_PER_IP` / `REGISTER_WINDOW_MINUTES`,
`FORGOT_PASSWORD_MAX_PER_IP` / `FORGOT_PASSWORD_WINDOW_MINUTES`, and
`RESEND_VERIFICATION_MAX_PER_IP` / `RESEND_VERIFICATION_WINDOW_MINUTES` env vars, and can
be disabled entirely with `RATE_LIMIT_ENABLED=false` (used by the test suite, since the
test client's fixed IP would otherwise throttle fixtures that register many accounts).
If you're behind a reverse proxy, make sure it sets `X-Forwarded-For` correctly — see
`app.utils.get_client_ip()`.

Passwords (on registration and reset) must be at least `PASSWORD_MIN_LENGTH` characters
(8 by default) and include a lowercase letter, an uppercase letter, a number, and a
special character (`app.forms.validate_password_complexity`).

## Question types & grading

Each question is `single` (exactly one correct option), `multi` (one or more correct
options — the admin's UI enforces at least one but not all four marked correct), or
`short` (free text). Grading (`Question.score_for()` in `app/models.py`) is all-or-
nothing per question by default: for `multi`, the student's selected set must exactly
match the correct set — selecting only some of the correct options, or any incorrect
one, scores zero for that question. Enabling **"Award partial credit"** on a test switches
`multi` scoring to proportional: `(correct picks − incorrect picks) / total correct
options`, floored at zero and scaled to the question's marks — so picking half the
correct options with none wrong earns half credit, and mixing in a wrong pick reduces it
back down (never below zero). This setting has no effect on `single` or `short`
questions, which stay strictly right-or-wrong. `short` is graded case-insensitively with
whitespace trimmed, so "Paris", "paris", and "  Paris  " all match but "Parris" won't —
this is exact-text matching, not semantic grading, so short-answer questions where
several phrasings could be correct aren't a great fit without a synonym list of your own.
Bulk CSV import supports a `question_type` column (`single`/`multi`/`short`, defaults to
`single`); for `multi`, join the correct letters with `+`, e.g. `a+c`.

### Per-question time limits

A question can optionally have a soft `time_limit_seconds`. All questions render on one
scrollable page rather than a one-at-a-time stepper, so this isn't a hard gate on
navigation — instead, each timed question counts down independently from the moment the
exam starts, and once it hits zero its inputs lock in place (visually greyed out) while
the rest of the exam continues normally on the overall exam timer. Locking uses
`readOnly`/click-blocking rather than the `disabled` attribute, specifically because
`disabled` inputs are excluded from the browser's `FormData` on submit — that would have
silently erased an answer the student picked before time ran out. Like the overall exam
timer, this is enforced client-side (`app/static/js/proctor.js`), not re-validated by the
server against a submission timestamp — consistent with, and no weaker than, the existing
overall-timer design.

## Question randomization & retakes

Set per test: `randomize_questions` shuffles both question order and each question's
option order, deterministically seeded from the attempt's token so a page refresh
mid-exam doesn't reshuffle anything — grading always uses the actual selected option
letter, so shuffled display order never affects scoring. An optional `question_pool_size`
draws that many questions at random per student out of a larger question set — so two
students can get genuinely different subsets, not just a different order of the same
ones — using the same deterministic seeding. `max_attempts` controls how many times a
student may attempt the test; the dashboard shows attempts used/remaining and offers
"Retake Test" once a previous attempt is graded.

## Plagiarism / answer-similarity detection

`app.similarity` compares every pair of submitted descriptive/coding answers to the same
question within a test, using `difflib.SequenceMatcher` on normalized text (coding
answers additionally get comments stripped before comparison, so two functionally
identical submissions that only differ in variable names or comments still score as
near-identical). Pairs at or above a configurable threshold (`SIMILARITY_THRESHOLD_DEFAULT`,
70% by default) are flagged for an examiner to confirm or dismiss from
`/admin/tests/<id>/plagiarism` — a flag is a signal, not a verdict, since two students can
legitimately give a similar answer to a short factual question.

## Exam Integrity Controls

- **Question pools** — see Question randomization above.
- **Single active session per account** (`SINGLE_SESSION_PER_ACCOUNT`) — a new login ends
  every other active session for that account; a stale browser tab gets logged out
  (with a message) on its next request rather than continuing to work silently.
- **Device/IP tracking & login anomaly detection** (`app.security`) — every login records
  its IP and user-agent (`LoginSession`); a login from an IP or device not seen in the
  account's recent history logs a `LoginSecurityEvent` (`new_location` / `new_device`),
  surfaced to admins at `/admin/security-log` (org-scoped) or `/ops/sessions` /
  `/ops/alerts` (platform-wide).
- **Optional VPN/proxy detection** (`VPN_DETECTION_ENABLED`) — checks a configured
  third-party IP-reputation API on login; off by default, and never blocks a login even
  when misconfigured or unreachable, only logs a `vpn_or_proxy_suspected` event.

## Warning-based disqualification

The simple, blunt rule most admins actually want: **a student may be warned a couple of
times, and the next rule violation after that disqualifies them** — regardless of which
rule it was. This is separate from, and sits on top of, two other things already in this
project that a reader familiar with the codebase should not confuse it with:

- The **Customizable Warning System** (`Test.proctoring_policy`, configured per test per
  event type) — lets an admin say e.g. "the first 2 `window_blur` events are just a
  warning, the 3rd flags it, and `phone_detected` should terminate immediately." That's
  fine-grained, per-event-type control, off by default.
- **`MAX_VIOLATIONS_BEFORE_TERMINATION`** — the pre-existing plain count of
  *violation*-severity events (not warnings) before the attempt is auto-terminated.
  Unaffected by anything below; a real violation still counts toward it exactly as before.

Warning-based disqualification tracks a single running total instead —
`Attempt.total_warning_count` — that goes up by one for *every* warning-severity event on
the attempt, no matter its type: a tab-switch warning and a no-face warning both count
against the same total. Once that total reaches the limit, the next warning-worthy event
disqualifies the student on the spot (`attempt.status = "terminated"`, with the reason
"Disqualified after using all N warnings allowed for this exam.") instead of being logged
as one more warning. The event itself is recorded as a violation, not a warning, and does
count toward `violation_count` (it's the one real violation that ended the exam).

- **Limit**: `MAX_WARNINGS` (env var, platform default is 2) or, per test, `Test.max_warnings`
  — set when creating or editing a test. Leave it blank to use the platform default; set it
  to `0` to turn this off for that one test (only real violations can end the exam, same as
  before this feature existed); set it to any other number to override the default.
- **What the student sees**: each warning's banner says how many are left ("1 warning left
  before you are disqualified" / "Final warning — one more will disqualify you"), and the
  alert shown when the exam ends now carries the server's actual reason instead of a
  generic "repeated proctoring violations" message — useful for a zero-tolerance
  termination too, not just this feature.
- **What a reviewer sees**: the attempt detail page and the incident-report PDF both show
  "Warnings used: X / N" next to the violation count.

Adds two columns (`tests.max_warnings`, `attempts.total_warning_count`) rather than whole
new tables. See "Upgrading an existing database" below for how those — and any other
column a previously-deployed database is missing — get added automatically at startup.

## Live Monitor & Appeals

**Live Monitor** (`/admin/live`, admin / examiner / proctor): a real-time roster of every attempt
currently in progress in your organization, highest risk first — student, test, elapsed time,
connection status ("No signal" once the exam tab has stopped sending heartbeats for
`LIVE_MONITOR_NO_SIGNAL_SECONDS`), violation count, risk level and the latest warning/violation.
It polls a JSON endpoint every few seconds (no websockets or broker, so it behaves the same across
multiple worker processes). Two interventions are available per row:

- **Message** — a short note that appears on the student's exam screen until they dismiss it. It is
  delivered through the exam page's existing heartbeat, so it arrives within one heartbeat
  interval (~15s), exactly once, and only to the tab that currently owns the attempt.
- **End** — terminates the attempt with a required reason, shown to the student and stored as
  `Ended by a proctor: <reason>`. It ends in the same state as an automatic termination (same
  notifications, result page and review queue) but does not count as a proctoring violation.

Both actions are recorded as `info`-severity events on the attempt's timeline (never scored) and in
the admin activity log.

**Appeals** — a student can contest a *terminated* attempt, or one that picked up violations, from
the result page (once per attempt, within `APPEAL_WINDOW_DAYS`, default 14). Staff decide from
`/admin/appeals` (admin / examiner / proctor): **granting** gives the student one extra attempt on
that test (via the existing `TestEligibility.extra_attempts`) and **denying** keeps the outcome;
either way the student is emailed with an optional reviewer note. The original attempt's record is
never altered, so the audit trail stays intact.

Both features add new tables only (`appeals`, `proctor_messages`), created automatically on startup.

## Review decisions & incident reports

**Reviewer decision** — on any finished attempt's detail page a reviewer (admin / examiner /
proctor) records a conclusion: **Cleared** (no misconduct found), **Confirmed** (misconduct), or
**Escalate** (needs follow-up). Notes are required for Confirmed and Escalate, so a finding always
carries its reasoning. There is one current decision per attempt; changing it updates it in place
and each change is written to the admin activity log. Reviewing is refused while the attempt is
still in progress (use the Live Monitor for mid-exam action).

The **Review Queue** now shows each attempt's decision and can be filtered to *Not yet reviewed*,
*Escalated*, *Confirmed* or *Cleared* — so "nobody has looked at this" is no longer
indistinguishable from "looked at and fine". The Appeals page shows the decision alongside each
appeal.

**Incident report (PDF)** — "Download incident report" on the attempt page exports a one-attempt
document for disciplinary cases, appeals or hand-offs: student/test/attempt summary, risk score
and reasons, the reviewer's decision and notes, the student's appeal (if any), and the full
proctoring event log. It is text-only by design — snapshots and recordings stay behind the app's
access checks instead of being copied into a file that travels by email. All free text is escaped
before rendering. Each export is written to the activity log.

Adds one new table (`attempt_reviews`), created automatically on startup.

## Interface & design

The UI is a single stylesheet (`app/static/css/style.css`, a token-based design system with a
light and a dark theme) plus a shared shell in `app/templates/base.html`. Because every page
inherits both, restyling the app means editing those two files rather than each template.

- **Shell.** Staff (admin / examiner / proctor / super_admin) get a grouped left sidebar; students
  get a top bar; the login/registration pages are a split screen with no nav. The sidebar's
  long settings group folds away, and below 900px it becomes a slide-over menu.
- **Live counts.** The sidebar shows exams in progress, finished attempts flagged but not yet
  reviewed, and pending appeals. They come from a small, best-effort context processor
  (`_inject_staff_counts` in `app/__init__.py`): three cheap `COUNT`s per staff page, and any
  failure just hides the badges rather than breaking the page.
- **Risk meter.** The segmented gauge for the 0-100 suspicion score (`.meter`, macro in
  `_components.html`) is used identically in the review queue, dashboard, live monitor, appeals
  and attempt pages. The score and level are always written out too, so colour is never the only
  signal. Table rows carry a matching severity edge (`.rail-low|medium|high|critical`).
- **Typography.** Newsreader (page titles and headline numerals), Instrument Sans (interface),
  IBM Plex Mono (scores, times, ids). All three are **self-hosted** under `app/static/fonts/`
  (SIL Open Font License, see `LICENSES.txt` there), so the app makes no third-party font
  requests and works on an offline network.
- **Icons** are inline SVG (`_icons.html`), not emoji or an icon font.
- **Accessibility.** Visible keyboard focus, a skip link, `prefers-reduced-motion` respected, and
  `aria-current` on the active navigation item.
- **Safe rendering of user data.** The live-alert bell builds its DOM with `textContent` — alert
  labels and student names are user-supplied and must never be interpolated into HTML.

## Multi-tenancy (Institution / Organization Management)

Every `Test`, `QuestionBankItem`, and non-`super_admin` `User` belongs to exactly one
`Organization`. Registration lets a new account join an existing (active) organization
from a dropdown, or — admin role only — create a brand-new one, which is how a new
institution bootstraps its first admin. A registration that doesn't specify an
organization at all falls back to a shared "Default Organization", so the flow stays
backward-compatible with anything that posts the older, org-less form.

Every list query and fetch-by-id route in `app/admin.py` is scoped to the caller's
organization (`app.utils.org_scope` / `ensure_same_org`) — an org's admin/examiner/
proctor can only ever see or act on their own organization's tests, users, results,
analytics, and logs; a cross-org attempt (even by guessing another org's numeric ID)
gets a 403, not just an empty list.

`super_admin` is a platform-level role (not tied to any organization — `User.org_id` is
`NULL`) that manages organizations themselves via `app/organizations.py`: create/activate/
deactivate organizations, view a per-org usage report (with CSV/PDF export — see
`app.org_reports`), and download that org's self-service JSON data export
(`app.org_export`). It's deliberately walled off from `content_access`/`review_access` —
a platform operator manages institutions, not any one institution's exam content — with
one narrow, explicit exception:

**Impersonation ("View Exam Content" support access)** — from an organization's detail
page, a super_admin can start a read-only support view of that org's tests, questions,
results, analytics, and proctor queue. This works by making `current_org_id()` resolve to
the impersonated org for the duration (see `app.utils.roles_required_or_impersonating`),
so every existing org-scoping check applies exactly as it would for that org's own admin
— including 403ing on any *other* org's data. Only `GET` requests are let through this
path: every mutating action (create/edit/delete/publish/grade/assign/...) still requires
actually being that org's own admin or examiner, and user management, security settings,
and retention policy remain completely off-limits regardless. A persistent banner shows
while impersonating, with a one-click "Stop Impersonating", and both the start and stop
are written to the activity log.

## System Monitoring & Operations

A `/ops` area, reachable only by `super_admin`:

- **Health dashboard** — uptime, a live DB connectivity check, disk usage, process
  memory, and platform-wide counts (organizations, users, tests, attempts, in-progress
  attempts, active sessions).
- **Active sessions** — every currently active login session across all organizations.
- **Storage usage** — disk usage broken down by category (recordings, snapshots, ID
  documents, backups) plus the database file size.
- **Failed proctoring sessions** — every auto-terminated attempt across all organizations.
- **Recording-storage management** — per-recording delete, or a bulk "delete everything
  older than N days" action.
- **Error monitoring** — `app.error_monitoring` logs genuinely unexpected exceptions
  (via a `got_request_exception` signal listener, not an error handler, so ordinary
  404/403s are never miscounted as bugs) to an `ErrorLog` table, reviewable and
  resolvable at `/ops/errors`.
- **Automated backups** (`app.backup`) — a straight file-copy of the sqlite database,
  triggerable from `/ops/backups` or the `flask backup-db` CLI command (meant for an
  external cron/Task Scheduler entry — this app has no background scheduler of its own).
  Only sqlite is handled directly; a server database (Postgres/MySQL via `DATABASE_URL`)
  should use that engine's own backup tooling instead.
- **Data retention/deletion policies** (`app.retention`) — a configurable retention
  window (in days) per category (recordings, snapshots, ended sessions, security events,
  activity log, notification log, error log), resolved as **org override → platform
  default → config.py built-in default**. An org's own admin can override their own
  windows from `/admin/retention`; a super_admin can set the platform default from
  `/ops/retention`, or override a specific org's from that org's detail page. Run via
  `flask apply-retention` (external cron) or the "Run Cleanup Now" button.
- **Real-time health alerts** (`app.alerting`) — disk usage and error-rate spikes each
  raise a `SystemAlert` and notify every `super_admin` by email (or a configured
  `ALERT_EMAIL_OVERRIDE` list) plus an optional Slack webhook (`SLACK_WEBHOOK_URL`).
  Error-rate spikes are checked **inline, right after every error is logged** — genuinely
  real-time, not just on the next periodic sweep — while disk usage is checked whenever
  the health dashboard loads and via the `flask check-health-alerts` CLI command. Each
  alert type is deduplicated: a sustained problem raises one notification, not one per
  check, until a human resolves it from `/ops/alerts`.

## Legal pages & consent

`/terms` and `/privacy` (`app/legal.py`) are public, unauthenticated pages, linked from every
page's footer and from the registration form. They accurately describe what this specific
codebase actually collects and stores (face descriptors, recordings, retention windows,
multi-tenant data isolation) — genuinely useful as a starting draft, but **not a substitute
for legal review**; both pages say so prominently. Registration includes a consent checkbox;
if checked, `User.terms_accepted_at` and `terms_version_accepted` (bumped via
`TERMS_VERSION`) record it. The checkbox is `required` in the actual HTML form but validated
as optional server-side, so a registration that skips it (e.g. a script hitting the endpoint
directly) isn't blocked — it just has no consent timestamp, which is the honest state rather
than a fabricated "accepted".

## Org-level branding

An organization's own admin (`/admin/branding`) or a super_admin on its behalf
(`/organizations/<id>/branding`) can upload a logo and set a primary accent color
(`app.branding`), applied to that org's users via a small CSS custom-property override and a
logo swap in the sidebar / top bar. The sidebar's pine background is fixed page chrome and
does not follow the accent color; buttons, links, focus rings and highlights do. Logo upload and color are two independent actions/forms, not one
combined submit — an HTML5 `type="color"` input can never be truly empty, so a shared submit
button would silently overwrite the org's color with black every time someone only meant to
upload a logo. Branding only ever applies **after** login; there's no per-org subdomain, so an
anonymous visitor's organization can't be known before they authenticate, and the login/
registration pages always show the platform's default look. A super_admin who's impersonating
an org (see Multi-tenancy above) sees that org's branding for the duration.

## CI

`.github/workflows/tests.yml` runs the `pytest` suite (matrix: Python 3.11 and 3.12) plus a
`compileall` sanity check on every push/PR to `main`/`master`.

## Known limitations (by design)

A few gaps are intentional scope boundaries for this rewrite, not oversights:

- **Gaze tracking and head-pose detection are lightweight heuristics, not calibrated
  computer-vision models.** They're built entirely from face-api.js's existing 68-point
  landmarks and classic techniques (dark-pupil centroid, geometric yaw/pitch ratios) —
  deliberately dependency-free (no new model weights, no server-side ML), but that means
  they're coarse: sized to catch sustained/repeated off-screen attention, not to produce
  research-grade gaze coordinates or a true 3D head-pose solve. See [AI Proctoring
  Signals](#ai-proctoring-signals) for exactly what each one measures.
- **Identity verification is same-person consistency, not government-ID verification.**
  Enrollment is self-service (see [Identity verification](#identity-verification)) — it
  proves the person at the camera during the exam matches whoever sat down and enrolled,
  not that either of them is the person the account claims to be. Binding an account to a
  real-world identity would mean integrating a third-party ID-verification/KYC provider
  (document capture + liveness + data-privacy handling for government ID images), which is
  a distinct product decision with its own vendor, cost, and compliance surface — out of
  scope here.
- **The Terms/Privacy pages are a draft, not legal sign-off.** They accurately describe what
  this codebase does today, but the specific regulatory obligations for a real deployment
  (GDPR, COPPA, state biometric-privacy statutes like Illinois' BIPA, etc.) depend on where
  you operate and who your students are — get them reviewed before relying on them.
- **Non-sqlite backups need `pg_dump`/`mysqldump` on the server** (see System Monitoring &
  Operations above) — this app shells out to those tools rather than reimplementing a native
  dump; if they're not installed, `/ops/backups` explains that clearly rather than silently
  producing an empty or broken backup. A managed database (RDS, Cloud SQL, etc.) should
  usually rely on its provider's own automated snapshots instead of this feature anyway.
- **Branding has no per-org custom domain/subdomain**, so it can only apply after login (see
  Org-level branding above) — an anonymous visitor's organization isn't knowable in advance.

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
