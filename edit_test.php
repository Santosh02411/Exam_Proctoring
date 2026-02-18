<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html"); exit;
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// fetch test id
$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if (!$test_id) {
    die("Missing test_id");
}
// handle POST update
$success = null;
$error = null;

f ($_SERVER['REQUEST_METHOD'] === 'POST') {
    // sanitize and prepare values
    $title = $conn->real_escape_string($_POST['title']);
    $description = $conn->real_escape_string($_POST['description']);
    $duration = (int)$_POST['duration_minutes'];
    $total_questions = (int)$_POST['total_questions'];
    $passing_marks = (int)$_POST['passing_marks'];
    $status = $conn->real_escape_string($_POST['status']);
    // For datetime fields, keep '' if empty so we can convert to NULL via NULLIF(?, '')
    $start_time = isset($_POST['start_time']) ? $_POST['start_time'] : '';
    $end_time   = isset($_POST['end_time'])   ? $_POST['end_time']   : '';
    // Use NULLIF(?, '') so empty string => SQL NULL
    $sql = "UPDATE tests
            SET title = ?, description = ?, duration_minutes = ?, total_questions = ?, passing_marks = ?,
                status = ?, start_time = NULLIF(?, ''), end_time = NULLIF(?, '')
            WHERE id = ?";
            $stmt = $conn->prepare($sql);
    if (!$stmt) {
        $error = "Prepare failed: " . $conn->error;
    }
    else {
        // Correct types:
        // title (s), description (s),
        // duration (i), total_questions (i), passing_marks (i),
        // status (s), start_time (s), end_time (s), test_id (i)
        $types = "ssiiisssi";
        $stmt->bind_param($types,
            $title, $description,
            $duration, $total_questions, $passing_marks,
            $status, $start_time, $end_time,
            $test_id
        );
     if ($stmt->execute()) {
            $success = "Test updated successfully.";
        } else {
            $error = "Update failed: " . $stmt->error;
        }
        $stmt->close();
    }
}
// load existing test
$tstmt = $conn->prepare("SELECT * FROM tests WHERE id = ? LIMIT 1");
$tstmt->bind_param("i", $test_id);
$tstmt->execute();

$tres = $tstmt->get_result()->fetch_assoc();
if (!$tres) die("Test not found.");
$tstmt->close();
?>



<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edit Test — <?= htmlspecialchars($tres['title']) ?></title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
</style>
</head>
<body>
    <div class="wrap">
    <div class="header">
      <div>
        <a class="back" href="manage_tests.php">← Back to tests</a>
        <h1 style="margin:8px 0 0 0">Edit Test</h1>
        <div class="small">Test code: <strong><?= htmlspecialchars($tres['test_id']) ?></strong></div>
      </div>
      <div style="display:flex;gap:8px">
        <a href="add_question.php?test_db_id=<?= (int)$tres['id'] ?>" class="btn ghost">Add Questions</a>
        <a href="view_test.php?test_id=<?= (int)$tres['id'] ?>" class="btn">View Test</a>
      </div>
    </div>





<script>
</script>
</body>
</html>