<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') { header("Location: login.html"); exit; }
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
$t = $conn->query("SELECT * FROM tests WHERE id=".$test_id)->fetch_assoc();
$questions = $conn->query("SELECT * FROM questions WHERE test_id=".$test_id);
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>View Test — <?= htmlspecialchars($t['title'] ?? 'Test') ?></title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#f6f8fb;
    --card:#ffffff;
    --muted:#6b7280;
    --accent:#7c3aed;
    --primary:#0b6ef6;
    --radius:12px;
    --shadow: 0 10px 30px rgba(11,22,50,0.06);
    font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    color-scheme: light;
  }
  *{box-sizing:border-box}
  body{ margin:0; background:linear-gradient(180deg,var(--bg),#f3f6fa); padding:28px; color:#071033; -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }

  .container{ max-width:1100px; margin:0 auto; display:grid; gap:18px; }

  header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .left { display:flex; gap:14px; align-items:center; }
  .back {
    display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px; text-decoration:none; color:var(--muted);
    background:var(--card); box-shadow:var(--shadow); font-weight:700;
  }
  .title { line-height:1; }
  .title h1 { margin:0; font-size:20px; }
  .title p { margin:4px 0 0 0; color:var(--muted); font-size:13px; }

  .actions { display:flex; gap:10px; align-items:center; }
  .btn {
    display:inline-flex; gap:8px; align-items:center; padding:9px 12px; border-radius:10px; text-decoration:none; font-weight:700; font-size:14px;
    background:var(--primary); color:#fff; box-shadow: 0 8px 20px rgba(11,110,246,0.12);
  }
  .btn.ghost { background:transparent; color:var(--primary); border:1px solid rgba(11,110,246,0.08); box-shadow:none }

  .meta-row { display:flex; gap:12px; align-items:center; color:var(--muted); font-size:13px; margin-top:6px; }

  .card { background:var(--card); border-radius:var(--radius); padding:18px; box-shadow:var(--shadow); }

  .test-details { display:flex; gap:18px; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; }
  .test-main { max-width:75%; }
  .test-side { min-width:220px; }

  .desc { margin-top:12px; color:#0b1220; line-height:1.6; white-space:pre-wrap; }

  .questions { display:grid; gap:12px; margin-top:6px; }
  .qcard {
    border-radius:10px;
    padding:14px;
    background:linear-gradient(180deg,#ffffff,#fbfdff);
    box-shadow: 0 6px 18px rgba(11,22,50,0.04);
    display:flex; flex-direction:column; gap:8px;
  }
  .qtop { display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .qno { font-weight:800; color:#071033; }
  .qtext { margin:6px 0 0 0; color:#0b1220; }
  .options { display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-top:8px; }
  .opt { padding:10px; border-radius:8px; background:#fff; border:1px solid #eef2f7; font-size:14px; color:#071033; }
  .correct { border-color: rgba(16,185,129,0.16); background:linear-gradient(90deg, rgba(16,185,129,0.04), #fff); }
  .meta { color:var(--muted); font-size:13px; }

  footer { margin-top:8px; display:flex; justify-content:flex-end; gap:10px; }

  /* responsive */
  @media (max-width:900px) {
    .test-main{ max-width:100%; }
    .options{ grid-template-columns: 1fr; }
  }

  /* small helpers */
  .pill { display:inline-block; padding:6px 8px; border-radius:999px; font-weight:700; font-size:12px; background:#eef2ff; color:var(--primary); }
  .muted{ color:var(--muted) }
</style>
</head>
<body>
  <div class="container">
    <header>
      <div class="left">
        <a class="back" href="admin_dashboard.php" title="Back to dashboard">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M15 18l-6-6 6-6" stroke="#0b1220" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Back
        </a>

        <div class="title">
          <h1><?= htmlspecialchars($t['title'] ?? 'Test') ?></h1>
          <div class="meta-row">
            <div class="muted">Test ID: <span class="pill"><?= (int)$t['id'] ?></span></div>
            <div class="muted">Duration: <strong><?= (int)$t['duration_minutes'] ?? '-' ?> mins</strong></div>
            <div class="muted">Total Qs: <strong><?= (int)$t['total_questions'] ?? '-' ?></strong></div>
            <div class="muted">Passing: <strong><?= (int)$t['passing_marks'] ?? '-' ?></strong></div>
          </div>
        </div>
      </div>

      <div class="actions" role="toolbar">
        <a class="btn" href="add_question.php?test_db_id=<?= (int)$t['id'] ?>">Add Question</a>
        <a class="btn ghost" href="edit_test.php?test_id=<?= (int)$t['id'] ?>">Edit Test</a>
      </div>
    </header>

    <div class="card test-details">
      <div class="test-main">
        <div class="desc"><?= nl2br(htmlspecialchars($t['description'] ?? 'No description provided.')) ?></div>

        <h3 style="margin-top:18px">Questions</h3>

        <div class="questions" aria-live="polite">
          <?php if ($questions && $questions->num_rows > 0): ?>
            <?php while($q=$questions->fetch_assoc()): ?>
              <div class="qcard" id="q<?= (int)$q['id'] ?>">
                <div class="qtop">
                  <div class="qno">Q<?= (int)$q['id'] ?></div>
                  <div class="meta">Marks: <strong><?= htmlspecialchars($q['marks']) ?></strong></div>
                </div>

                <div class="qtext"><?= htmlspecialchars($q['question_text']) ?></div>

                <div class="options" role="list">
                  <div class="opt <?= $q['correct_answer'] === 'a' ? 'correct' : '' ?>">A — <?= htmlspecialchars($q['option_a']) ?></div>
                  <div class="opt <?= $q['correct_answer'] === 'b' ? 'correct' : '' ?>">B — <?= htmlspecialchars($q['option_b']) ?></div>
                  <div class="opt <?= $q['correct_answer'] === 'c' ? 'correct' : '' ?>">C — <?= htmlspecialchars($q['option_c']) ?></div>
                  <div class="opt <?= $q['correct_answer'] === 'd' ? 'correct' : '' ?>">D — <?= htmlspecialchars($q['option_d']) ?></div>
                </div>

                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;">
                  <div class="meta">Correct: <strong><?= htmlspecialchars(strtoupper($q['correct_answer'])) ?></strong></div>
                  <div class="muted">Created: <?= htmlspecialchars($q['created_at'] ?? '-') ?></div>
                </div>
              </div>
            <?php endwhile; ?>
          <?php else: ?>
            <div class="muted" style="padding:18px; background:#fff; border-radius:10px; box-shadow:var(--shadow);">No questions found for this test. Use <strong>Add Question</strong> to start adding MCQs.</div>
          <?php endif; ?>
        </div>
      </div>

      <aside class="test-side">
        <div class="card">
          <h4 style="margin:0 0 8px 0">Test Summary</h4>
          <div class="muted" style="margin-bottom:8px">Quick overview</div>
          <div style="display:grid;gap:8px">
            <div><strong>Title:</strong> <?= htmlspecialchars($t['title'] ?? '-') ?></div>
            <div><strong>Duration:</strong> <?= (int)$t['duration_minutes'] ?? '-' ?> minutes</div>
            <div><strong>Total Questions:</strong> <?= (int)$t['total_questions'] ?? '-' ?></div>
            <div><strong>Passing Marks:</strong> <?= (int)$t['passing_marks'] ?? '-' ?></div>
            <div><strong>Status:</strong>
              <?php if (($t['status'] ?? '') === 'published'): ?>
                <span style="color:var(--primary); font-weight:800; margin-left:6px">Published</span>
              <?php else: ?>
                <span style="color:var(--muted); font-weight:700; margin-left:6px">Draft</span>
              <?php endif; ?>
            </div>
            <div><strong>Created By:</strong> <?= (int)$t['created_by'] ?? '-' ?></div>
            <div><strong>Start:</strong> <span class="muted"><?= htmlspecialchars($t['start_time'] ?? 'Not set') ?></span></div>
            <div><strong>End:</strong> <span class="muted"><?= htmlspecialchars($t['end_time'] ?? 'Not set') ?></span></div>
          </div>
        </div>

        <div style="height:12px"></div>

        <div class="card">
          <h4 style="margin:0 0 8px 0">Actions</h4>
          <div style="display:flex;flex-direction:column;gap:10px;margin-top:8px">
            <a class="btn" href="create_test.php">Create New Test</a>
            <a class="btn ghost" href="assign_students.php?test_id=<?= (int)$t['id'] ?>">Assign Students</a>
            <a class="btn ghost" href="view_results.php?test_id=<?= (int)$t['id'] ?>">View Results</a>
          </div>
        </div>
      </aside>
    </div>

  </div>
</body>
</html>
