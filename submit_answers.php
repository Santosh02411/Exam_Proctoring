<?php
session_start();
if (!isset($_SESSION['user_id'])) die("Login required");
$uid = $_SESSION['user_id'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

if ($_SERVER['REQUEST_METHOD'] !== 'POST') die("Invalid");

// sanitize
$test_id = (int)$_POST['test_id'];
$attempt_token = $conn->real_escape_string($_POST['attempt_token']);

// fetch all questions for this test
$qres = $conn->query("SELECT id, correct_answer, marks FROM questions WHERE test_id=".$test_id);
$questions = [];
$total_marks = 0;
while ($r = $qres->fetch_assoc()) {
    $questions[$r['id']] = $r;
    $total_marks += (int)$r['marks'];
}

// insert each answer (use transaction)
$conn->begin_transaction();
try {
    $ins = $conn->prepare("INSERT INTO test_submissions (test_id, student_id, question_id, selected_answer, attempt_token) VALUES (?, ?, ?, ?, ?)");
    foreach ($questions as $qid => $meta) {
        $field = "q_".$qid;
        $sel = isset($_POST[$field]) ? $_POST[$field] : NULL;
        if ($sel !== NULL) $sel = $conn->real_escape_string($sel);
        $ins->bind_param("iiiss", $test_id, $uid, $qid, $sel, $attempt_token);
        $ins->execute();
    }

    // compute score
    $score = 0;
    $per_question = []; // store for internal use only (not shown to student)
    foreach ($questions as $qid => $meta) {
        $sel = null;
        if (isset($_POST["q_".$qid])) $sel = $_POST["q_".$qid];
        $earned = 0;
        if ($sel !== null && $sel === $meta['correct_answer']) {
            $score += (int)$meta['marks'];
            $earned = (int)$meta['marks'];
        }
        $per_question[$qid] = [
            'selected' => $sel,
            'correct'  => $meta['correct_answer'],
            'marks'    => (int)$meta['marks'],
            'earned'   => $earned
        ];
    }

    $passing_marks_row = $conn->query("SELECT passing_marks, title FROM tests WHERE id=".$test_id)->fetch_assoc();
    $passing_marks = (int)$passing_marks_row['passing_marks'];
    $test_title = $passing_marks_row['title'] ?? 'Test';

    $passed = $score >= $passing_marks;

    $rstmt = $conn->prepare("INSERT INTO test_results (test_id, student_id, attempt_token, score, total_marks, passed) VALUES (?, ?, ?, ?, ?, ?)");
    $rstmt->bind_param("iisiii", $test_id, $uid, $attempt_token, $score, $total_marks, $passed);
    $rstmt->execute();

    $conn->commit();

    // At this point answers & score saved.
    // We will render a student-facing result page that DOES NOT reveal answers.
} catch (Exception $e) {
    $conn->rollback();
    $errMsg = $e->getMessage();
    ?>
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>Submission Error</title>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>
        body{font-family:Inter,system-ui,Arial;margin:0;background:#f6f8fb;color:#071033;padding:28px}
        .card{max-width:760px;margin:40px auto;background:#fff;padding:20px;border-radius:12px;box-shadow:0 12px 30px rgba(11,22,50,0.06)}
        h1{margin:0 0 8px 0}
        p{color:#6b7280}
        a.btn{display:inline-block;padding:10px 14px;background:#0b6ef6;color:#fff;border-radius:10px;text-decoration:none}
        pre{background:#f3f5f9;padding:12px;border-radius:8px;overflow:auto}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Submission failed</h1>
        <p>Sorry — something went wrong while saving your answers. Please try again or contact support.</p>
        <p class="small">Error (for debugging):</p>
        <pre><?= htmlspecialchars($errMsg) ?></pre>
        <p><a class="btn" href="student_dashboard.php">Back to dashboard</a></p>
      </div>
    </body>
    </html>
    <?php
    exit;
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Results — <?= htmlspecialchars($test_title) ?></title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f6f8fb; --card:#ffffff; --muted:#6b7280; --primary:#0b6ef6; --success:#10b981; --danger:#ef4444; --radius:12px;
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    }
    *{box-sizing:border-box}
    body{margin:0;background:linear-gradient(180deg,var(--bg),#f3f6fa);color:#071033;padding:28px}
    .wrap{max-width:980px;margin:0 auto}
    .card{background:var(--card);border-radius:12px;padding:20px;box-shadow:0 12px 30px rgba(11,22,50,0.06)}
    h1{margin:0 0 6px 0}
    .meta{color:var(--muted);margin-bottom:12px}
    .score{font-size:28px;font-weight:800;margin:8px 0}
    .pass{color:var(--success);font-weight:800}
    .fail{color:var(--danger);font-weight:800}
    .actions{margin-top:14px;display:flex;gap:10px}
    .btn{padding:10px 14px;border-radius:10px;background:var(--primary);color:#fff;text-decoration:none;font-weight:700}
    .btn.ghost{background:transparent;color:var(--primary);border:1px solid rgba(11,110,246,0.08)}
    .small{font-size:13px;color:var(--muted)}
    .summary{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px}
    .pill{background:#fbfdff;padding:10px;border-radius:10px;font-weight:700}
    ul.qstatus{margin:12px 0 0 0;padding:0;list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
    ul.qstatus li{background:#fff;padding:8px 10px;border-radius:8px;border:1px solid #eef4ff;color:var(--muted);font-weight:700}
    .answered{background:linear-gradient(90deg,#ecfdf5,#f0fdf4);color:var(--success);border-color:rgba(16,185,129,0.12)}
    .unanswered{opacity:0.7}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card" role="main" aria-live="polite">
      <h1>Test submitted</h1>
      <div class="meta">Test: <strong><?= htmlspecialchars($test_title) ?></strong> — Attempt ID: <strong><?= htmlspecialchars($attempt_token) ?></strong></div>

      <div class="score">
        Your score: <span><?= (int)$score ?> / <?= (int)$total_marks ?></span>
        &nbsp; — &nbsp;
        <span class="<?= $passed ? 'pass' : 'fail' ?>"><?= $passed ? 'Passed' : 'Failed' ?></span>
      </div>

      <div class="summary">
        <div class="pill small">Total questions: <?= count($questions) ?></div>
        <div class="pill small">Passing marks: <?= (int)$passing_marks ?></div>
        <div class="pill small">Attempt token: <?= htmlspecialchars($attempt_token) ?></div>
      </div>

      <div class="actions" style="margin-top:14px;">
        <a class="btn" href="student_dashboard.php">Back to Dashboard</a>
       \
      </div>

      <h2 style="margin-top:20px">Question status</h2>
      <p class="small">For privacy and fairness, individual answers are not shown here. Instructors can view detailed results.</p>

      <ul class="qstatus" aria-label="Question answered status">
        <?php
          // show only answered/unanswered status (no answers or correctness)
          foreach ($questions as $qid => $meta):
            $answered = isset($per_question[$qid]) && $per_question[$qid]['selected'] !== null && $per_question[$qid]['selected'] !== '';
        ?>
          <li class="<?= $answered ? 'answered' : 'unanswered' ?>">
            Q<?= htmlspecialchars(array_search($qid, array_keys($questions)) + 1) ?><!-- not exposing answers -->
            &nbsp; — &nbsp; <?= $answered ? 'Answered' : 'Unanswered' ?>
          </li>
        <?php endforeach; ?>
      </ul>

      <p class="small" style="margin-top:12px">Note: This page confirms your answers were recorded. If you think there was a problem, contact your instructor with the attempt ID above.</p>
    </div>
  </div>
</body>
</html>
