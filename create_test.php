<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    die("Access denied");
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $test_id = $conn->real_escape_string($_POST['test_id']);
    $title = $conn->real_escape_string($_POST['title']);
    $description = $conn->real_escape_string($_POST['description']);
    $duration = (int)$_POST['duration_minutes'];
    $total_questions = (int)$_POST['total_questions'];
    $passing_marks = (int)$_POST['passing_marks'];
    $status = $conn->real_escape_string($_POST['status']);
    $start_time = $_POST['start_time'] ?: NULL;
    $end_time = $_POST['end_time'] ?: NULL;
    $created_by = $_SESSION['user_id'];

    $stmt = $conn->prepare("INSERT INTO tests (test_id, title, description, duration_minutes, total_questions, passing_marks, status, start_time, end_time, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
    $stmt->bind_param("sssiiiisss", $test_id, $title, $description, $duration, $total_questions, $passing_marks, $status, $start_time, $end_time, $created_by);
    if ($stmt->execute()) {
        echo "Test created. <a href='add_question.php?test_db_id=".$stmt->insert_id."'>Add Questions</a>";
    } else {
        echo "Error: ".$stmt->error;
    }
}
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
  <title>Create Test — Admin</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg: #f6f8fb;
      --card: #ffffff;
      --muted: #6b7280;
      --primary: #0b6ef6;
      --accent: #7c3aed;
      --radius: 12px;
      --shadow: 0 12px 30px rgba(11,22,50,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    }
    *{box-sizing:border-box}
    body{ margin:0; background:linear-gradient(180deg,var(--bg),#f3f6fa); color:#071033; padding:28px; -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }
    .wrap{ max-width:980px; margin:0 auto; display:grid; gap:18px; }
    header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .back { text-decoration:none; color:var(--muted); display:inline-flex; gap:8px; align-items:center; padding:8px 12px; border-radius:10px; background:var(--card); box-shadow:var(--shadow); font-weight:700; }
    h1{ margin:0; font-size:20px; }
    .lead{ color:var(--muted); margin-top:6px; font-size:13px }
    .card { background:var(--card); border-radius:var(--radius); padding:20px; box-shadow:var(--shadow); }
    form.grid{ display:grid; grid-template-columns: 1fr 360px; gap:18px; align-items:start; }
    @media (max-width:980px){ form.grid{ grid-template-columns: 1fr; } }
    .field{ display:flex; flex-direction:column; gap:8px; margin-bottom:6px; }
    label{ font-weight:700; font-size:13px; color:#0b1220; }
    .hint{ font-size:13px; color:var(--muted); }
     input[type="text"], input[type="number"], input[type="datetime-local"], textarea, select {
      width:100%; padding:10px 12px; border-radius:10px; border:1px solid #e6eef8; font-size:14px; background:#fff; color:#071033;
      box-shadow: inset 0 1px 0 rgba(0,0,0,0.02);
    }
    textarea { min-height:120px; resize:vertical; }

    .kpi { display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }
    .kpi .pill { background:#fbfdff; padding:8px 12px; border-radius:999px; font-weight:700; color:var(--muted); box-shadow: 0 6px 12px rgba(11,22,50,0.04); }
     .actions { display:flex; gap:10px; margin-top:10px; }
    .btn {
      padding:10px 14px; border-radius:10px; font-weight:700; text-decoration:none; color:#fff; background:var(--primary); border:none; cursor:pointer; box-shadow:0 8px 20px rgba(11,110,246,0.12);
    }
      .row { display:flex; gap:8px; }
    .row .col { flex:1 }
  </style>
</head>

<body>
  <div class="wrap">
    <header>
      <div>
        <a class="back" href="admin_dashboard.php" title="Back to dashboard">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M15 18l-6-6 6-6" stroke="#0b1220" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Back
        </a>
        <h1 style="margin-top:10px">Create New Test</h1>
        <div class="lead">Define the test details — create, then add questions and assign students.</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <a class="btn ghost" href="manage_tests.php" style="padding:8px 12px">Manage Tests</a>
        <a class="btn" href="add_question.php" style="padding:8px 12px">Add Question</a>
      </div>
    </header>
    <div class="card">
      <form class="grid" method="post" id="createTestForm" onsubmit="return handleSubmit();">
        <!-- left (main form) -->
        <div>
          <div class="field">
            <label for="test_id">Test Code</label>
            <input id="test_id" name="test_id" type="text" placeholder="EXAM2025" required>
            <div class="hint">A short unique code for the test (used internally).</div>
          </div>
          <div class="field">
            <label for="title">Title</label>
            <input id="title" name="title" type="text" placeholder="e.g. Introduction to AI" required>
          </div>
          <div class="field">
            <label for="description">Description</label>
            <textarea id="description" name="description" placeholder="Brief description or instructions (optional)"></textarea>
          </div>
          <div class="row">
            <div class="col field">
              <label for="duration_minutes">Duration (minutes)</label>
              <input id="duration_minutes" name="duration_minutes" type="number" min="1" value="30" required>
            </div>
            <div class="col field">
              <label for="total_questions">Total Questions</label>
              <input id="total_questions" name="total_questions" type="number" min="1" value="10" required>
            </div>
          </div>










