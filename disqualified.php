<?php
// disqualified.php
session_start();

// If you want to allow anonymous disqualification pages, remove this check.
// But for safety we require the student to be logged in.
if (!isset($_SESSION['user_id'])) {
    // If not logged in, show a simple message and stop.
    http_response_code(403);
    echo "<h2>Access denied</h2><p>You must be logged in to view this page.</p>";
    exit;
}

$student_id = (int)$_SESSION['user_id'];

// Accept test_id / attempt_token / reason from GET or POST
$test_id = isset($_REQUEST['test_id']) ? (int)$_REQUEST['test_id'] : null;
$attempt_token = isset($_REQUEST['attempt_token']) ? trim($_REQUEST['attempt_token']) : null;
$reason = isset($_REQUEST['reason']) ? trim($_REQUEST['reason']) : 'disqualified_by_proctor';

// DB connection — adjust credentials if needed
$host = 'localhost';
$user = 'root';
$pass = '';
$db   = 'exam_proctoring';

$conn = new mysqli($host, $user, $pass, $db);
if ($conn->connect_error) {
    http_response_code(500);
    echo "<h2>Server error</h2><p>Could not connect to database.</p>";
    exit;
}

// Create proctor_violations table if it doesn't exist
$create_sql = "
CREATE TABLE IF NOT EXISTS proctor_violations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  test_id INT NULL,
  student_id INT NOT NULL,
  attempt_token VARCHAR(255) NULL,
  reason VARCHAR(255) NOT NULL,
  details TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (test_id),
  INDEX (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
";
$conn->query($create_sql); // ignore errors for now

// If test_id provided, remove eligibility for this student for that test
$eligibility_removed = false;
if ($test_id) {
    $del = $conn->prepare("DELETE FROM test_eligibility WHERE test_id = ? AND student_id = ?");
    if ($del) {
        $del->bind_param("ii", $test_id, $student_id);
        $del->execute();
        // check affected rows
        if ($del->affected_rows > 0) $eligibility_removed = true;
        $del->close();
    }
}

// Insert a record into proctor_violations for audit
$ins = $conn->prepare("INSERT INTO proctor_violations (test_id, student_id, attempt_token, reason, details) VALUES (?, ?, ?, ?, ?)");
$details = null;
if ($ins) {
    // try to record some helpful details (user agent + referer). Keep length reasonable.
    $ua = isset($_SERVER['HTTP_USER_AGENT']) ? $_SERVER['HTTP_USER_AGENT'] : '';
    $ref = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : '';
    $details = "UA: " . substr($ua,0,800) . "\nREF: " . substr($ref,0,800);
    $ins->bind_param("iisss", $test_id, $student_id, $attempt_token, $reason, $details);
    $ins->execute();
    $ins->close();
}

// Optionally: mark any in-progress submissions as void/failed.
// If you have a test_submissions or test_results table and want to mark attempts invalid,
// add appropriate UPDATE queries here (not implemented because schema may vary).

// Build a user-facing message
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Disqualified — Exam Proctoring</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body{font-family:Arial,Helvetica,sans-serif;background:#f6f8fb;color:#071033;padding:28px}
    .card{max-width:760px;margin:30px auto;background:#fff;padding:24px;border-radius:12px;box-shadow:0 12px 40px rgba(2,6,23,0.08)}
    h1{margin-top:0;color:#b91c1c}
    p{color:#374151}
    .meta{color:#6b7280;font-size:14px}
    a.btn{display:inline-block;padding:10px 14px;background:#0b6ef6;color:#fff;border-radius:8px;text-decoration:none;margin-top:10px}
    .small{font-size:13px;color:#6b7280;margin-top:8px}
  </style>
</head>
<body>
  <div class="card" role="main">
    <h1>Exam attempt terminated — You have been disqualified</h1>

    <?php if ($test_id): ?>
      <p class="meta">Test ID: <strong><?=htmlspecialchars($test_id)?></strong></p>
      <?php if ($eligibility_removed): ?>
        <p>The ability to take this test has been removed from your account. You will no longer be able to access this exam.</p>
      <?php else: ?>
        <p>We were unable to find or remove your eligibility row (it may already have been removed).</p>
      <?php endif; ?>
    <?php else: ?>
      <p>Your attempt has been terminated and your access to the test has been revoked.</p>
    <?php endif; ?>

    <p class="small">Reason recorded: <strong><?= htmlspecialchars($reason) ?></strong></p>
    <?php if ($attempt_token): ?>
      <p class="small">Attempt token: <code><?= htmlspecialchars($attempt_token) ?></code></p>
    <?php endif; ?>

    <p class="small">If you believe this is a mistake, contact the exam administrator with your name and a screenshot of this page.</p>

    <a class="btn" href="student_dashboard.php">Back to dashboard</a>
  </div>
</body>
</html>

<?php
// close connection
$conn->close();
