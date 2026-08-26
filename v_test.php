<?php
// view_test.php
session_start();
if (!isset($_SESSION['user_id'])) {
    header('Location: login.html');
    exit;
}
$viewer_id = (int)$_SESSION['user_id'];
$viewer_role = $_SESSION['role'] ?? 'student';

// DB connection - adjust creds if needed
$conn = new mysqli("localhost", "root", "", "exam_proctoring");
if ($conn->connect_error) die("DB error: " . $conn->connect_error);

// read test_id param
$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if ($test_id <= 0) {
    http_response_code(400);
    echo "Missing or invalid test_id";
    exit;
}

// If viewer is student, verify eligibility
if ($viewer_role !== 'admin') {
    $elig = $conn->prepare("SELECT 1 FROM test_eligibility WHERE test_id = ? AND student_id = ? LIMIT 1");
    $elig->bind_param("ii", $test_id, $viewer_id);
    $elig->execute();
    $elig->store_result();
    if ($elig->num_rows === 0) {
        http_response_code(403);
        echo "You are not eligible to view this test.";
        exit;
    }
    $elig->close();
}

// load test metadata and creator name
$stmt = $conn->prepare("
    SELECT t.id, t.test_id AS code, t.title, t.description, t.duration_minutes, t.total_questions,
           t.passing_marks, t.status, t.start_time, t.end_time, t.created_by, t.created_at,
           u.name AS creator_name, u.email AS creator_email
    FROM tests t
    LEFT JOIN users u ON u.id = t.created_by
    WHERE t.id = ? LIMIT 1
");
$stmt->bind_param("i", $test_id);
$stmt->execute();
$res = $stmt->get_result();
if ($res->num_rows === 0) {
    http_response_code(404);
    echo "Test not found.";
    exit;
}
$test = $res->fetch_assoc();
$stmt->close();

// count questions (just metadata)
$qcountStmt = $conn->prepare("SELECT COUNT(*) AS c, COALESCE(SUM(marks),0) AS total_marks FROM questions WHERE test_id = ?");
$qcountStmt->bind_param("i", $test_id);
$qcountStmt->execute();
$qc = $qcountStmt->get_result()->fetch_assoc();
$qcountStmt->close();

$conn->close();

// helper to safely print
function h($s){ return htmlspecialchars($s, ENT_QUOTES|ENT_SUBSTITUTE, 'UTF-8'); }
function fmtDt($v){
    if (!$v) return '-';
    $d = strtotime($v);
    if (!$d) return h($v);
    return date('Y-m-d H:i', $d);
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>View Test — <?= h($test['title']) ?></title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg:#f6f8fb; --card:#fff; --muted:#6b7280; --primary:#0b6ef6; --radius:12px; --shadow:0 12px 30px rgba(11,22,50,0.06);
      font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    }
    *{box-sizing:border-box}
    body{margin:0;background:linear-gradient(180deg,var(--bg),#f3f6fa);color:#071033;padding:22px}
    .wrap{max-width:980px;margin:0 auto;display:grid;gap:16px}
    .top {display:flex;justify-content:space-between;align-items:center;gap:12px}
    a.back{color:var(--muted);text-decoration:none;padding:8px 12px;background:var(--card);border-radius:10px;box-shadow:var(--shadow)}
    .card{background:var(--card);padding:18px;border-radius:var(--radius);box-shadow:var(--shadow)}
    h1{margin:0;font-size:20px}
    .meta{color:var(--muted);font-size:13px;margin-top:6px}
    dl{display:grid;grid-template-columns:160px 1fr;gap:10px 20px;margin:16px 0}
    dt{color:var(--muted);font-weight:700}
    dd{margin:0}
    .desc{padding:12px;border-radius:8px;background:#fbfdff;border:1px solid #eef4ff;color:#0b1220}
    .actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
    .btn{padding:8px 12px;border-radius:10px;background:var(--primary);color:#fff;border:0;cursor:pointer;font-weight:700}
    .btn.ghost{background:transparent;color:var(--primary);border:1px solid rgba(11,110,246,0.08)}
    .small{font-size:13px;color:var(--muted)}
    @media (max-width:600px){ dl{grid-template-columns:120px 1fr} }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <a class="back" href="<?= ($viewer_role==='admin') ? 'admin_dashboard.php' : 'student_dashboard.php' ?>">&larr; Back</a>
      </div>
      <div style="text-align:right">
        <div class="small">Viewing as: <strong><?= h($viewer_role) ?></strong></div>
      </div>
    </div>

    <div class="card" role="region" aria-labelledby="title">
      <h1 id="title"><?= h($test['title']) ?></h1>
      <div class="meta">Test code: <strong><?= h($test['code']) ?></strong> &nbsp; • &nbsp; Created: <?= h(fmtDt($test['created_at'])) ?></div>

      <dl>
        <dt>Description</dt>
        <dd><div class="desc"><?= nl2br(h($test['description'] ?: '—')) ?></div></dd>

        <dt>Duration</dt>
        <dd><?= ($test['duration_minutes'] ? h($test['duration_minutes']) . ' minutes' : '—') ?></dd>

        <dt>Total questions</dt>
        <dd><?= (int)$test['total_questions'] ?> (DB count: <?= (int)$qc['c'] ?>)</dd>

        <dt>Total marks</dt>
        <dd><?= (int)$qc['total_marks'] ?> (passing: <?= (int)$test['passing_marks'] ?>)</dd>

        <dt>Status</dt>
        <dd><?= h($test['status'] ?: '—') ?></dd>

        <dt>Start time</dt>
        <dd><?= h(fmtDt($test['start_time'])) ?></dd>

        <dt>End time</dt>
        <dd><?= h(fmtDt($test['end_time'])) ?></dd>

        <dt>Created by</dt>
        <dd><?= h($test['creator_name'] ?: 'User #' . (int)$test['created_by']) ?> <?= $test['creator_email'] ? '<div class="small">' . h($test['creator_email']) . '</div>' : '' ?></dd>

        <dt>Notes</dt>
        <dd class="small">Only test metadata is visible here. Questions and answers are not shown on this page.</dd>
      </dl>

      <div class="actions">
        <?php if ($viewer_role === 'admin'): ?>
          <a class="btn ghost" href="edit_test.php?test_id=<?= (int)$test['id'] ?>">Edit</a>
          <a class="btn" href="add_question.php?test_db_id=<?= (int)$test['id'] ?>">Add Questions</a>
        <?php else: ?>
          <a class="btn" href="start_test.php?test_id=<?= (int)$test['id'] ?>">Start Test</a>
        <?php endif; ?>
      </div>
    </div>
  </div>
</body>
</html>
