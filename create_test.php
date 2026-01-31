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



      .row { display:flex; gap:8px; }
    .row .col { flex:1 }
  </style>
</head>











