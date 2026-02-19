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
<style>
:root{
  --bg:#f6f8fb; --card:#fff; --muted:#6b7280; --primary:#0b6ef6; --radius:12px; --shadow:0 12px 30px rgba(11,22,50,0.06);
  font-family:'Inter',system-ui,Arial;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,var(--bg),#f3f6fa);color:#071033;padding:22px}
.wrap{max-width:980px;margin:0 auto;display:grid;gap:16px}

.header{display:flex;justify-content:space-between;align-items:center;gap:12px}
.back{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;background:var(--card);box-shadow:var(--shadow);text-decoration:none;color:var(--muted);font-weight:700}
.card{background:var(--card);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow)}
.form-grid{display:grid;grid-template-columns:1fr 300px;gap:18px}
@media (max-width:920px){ .form-grid{grid-template-columns:1fr} }
.field{display:flex;flex-direction:column;gap:8px;margin-bottom:8px}
label{font-weight:700;color:#0b1220}
input,textarea,select{padding:10px;border-radius:10px;border:1px solid #e6eef8;font-size:14px;background:#fff}
textarea{min-height:120px;resize:vertical}
.actions{display:flex;gap:10px;justify-content:flex-end;margin-top:10px}
.btn{padding:10px 14px;border-radius:10px;background:var(--primary);color:#fff;text-decoration:none;border:none;font-weight:800;cursor:pointer}
.btn.ghost{background:transparent;border:1px solid rgba(11,110,246,0.08);color:var(--primary)}
.small{font-size:13px;color:var(--muted)}
.success{color:#10b981;font-weight:700}
.error{color:#ef4444;font-weight:700}
.panel{padding:12px;border-radius:10px;background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:0 6px 18px rgba(11,22,50,0.04)}
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
    <div class="card">
      <?php if ($success): ?><div class="success" style="margin-bottom:10px"><?= htmlspecialchars($success) ?></div><?php endif; ?>
      <?php if ($error): ?><div class="error" style="margin-bottom:10px"><?= htmlspecialchars($error) ?></div><?php endif; ?>
        <form method="post" class="form-grid" onsubmit="return validateEdit();">
        <div>
          <div class="field">
            <label for="title">Title</label>
            <input id="title" name="title" type="text" value="<?= htmlspecialchars($tres['title']) ?>" required>
          </div>
          <div class="field">
            <label for="description">Description</label>
            <textarea id="description" name="description"><?= htmlspecialchars($tres['description']) ?></textarea>
          </div>
          <div style="display:flex;gap:12px">
            <div class="field" style="flex:1">
              <label for="duration_minutes">Duration (minutes)</label>
              <input id="duration_minutes" name="duration_minutes" type="number" min="1" value="<?= (int)$tres['duration_minutes'] ?>" required>
            </div>
            <div class="field" style="width:160px">
              <label for="total_questions">Total questions</label>
              <input id="total_questions" name="total_questions" type="number" min="1" value="<?= (int)$tres['total_questions'] ?>" required>
            </div>
          </div>
          <div style="display:flex;gap:12px">
            <div class="field" style="flex:1">
              <label for="passing_marks">Passing marks</label>
              <input id="passing_marks" name="passing_marks" type="number" min="0" value="<?= (int)$tres['passing_marks'] ?>" required>
            </div>
            <div class="field" style="width:160px">
              <label for="status">Status</label>
              <select id="status" name="status" required>
                <option value="draft" <?= $tres['status']==='draft' ? 'selected' : '' ?>>Draft</option>
                <option value="published" <?= $tres['status']==='published' ? 'selected' : '' ?>>Published</option>
              </select>
            </div>
          </div>
          <div style="display:flex;gap:12px">
            <div class="field" style="flex:1">
              <label for="start_time">Start time (optional)</label>
              <input id="start_time" name="start_time" type="datetime-local" value="<?= $tres['start_time'] ? date('Y-m-d\TH:i', strtotime($tres['start_time'])) : '' ?>">
            </div>
             <div class="field" style="flex:1">
              <label for="end_time">End time (optional)</label>
              <input id="end_time" name="end_time" type="datetime-local" value="<?= $tres['end_time'] ? date('Y-m-d\TH:i', strtotime($tres['end_time'])) : '' ?>">
            </div>
          </div>
          <div class="actions">
            <button type="submit" class="btn">Save Changes</button>
            <a href="view_test.php?test_id=<?= (int)$tres['id'] ?>" class="btn ghost">Cancel</a>
          </div>
        </div>
        <aside>
          <div class="panel">
            <h4 style="margin:0 0 8px 0">Test Summary</h4>
            <div class="small">Code: <strong><?= htmlspecialchars($tres['test_id']) ?></strong></div>
            <div class="small">Created at: <strong><?= htmlspecialchars($tres['created_at']) ?></strong></div>
            <div style="margin-top:8px" class="small">Created by (ID): <strong><?= (int)$tres['created_by'] ?></strong></div>
          </div>
          <div class="panel" style="margin-top:12px">
            <h4 style="margin:0 0 8px 0">Danger zone</h4>
            <form method="post" action="manage_tests.php" onsubmit="return confirm('Delete this test? This action cannot be undone.');">
              <input type="hidden" name="delete_test_id" value="<?= (int)$tres['id'] ?>">
              <button type="submit" class="btn ghost" style="background:#fff;border:1px solid #f3d0d6;color:#b91c1c">Delete Test</button>
            </form>
          </div>
        </aside>
      </form>
    </div>
  </div>

<script>
function validateEdit(){
  const st = document.getElementById('start_time').value;
  const en = document.getElementById('end_time').value;
  if (st && en && new Date(st) >= new Date(en)) {
    alert('End time must be after start time.');
    return false;
  }
  return true;
}
<script>
function validateEdit(){
  const st = document.getElementById('start_time').value;
  const en = document.getElementById('end_time').value;
  if (st && en && new Date(st) >= new Date(en)) {
    alert('End time must be after start time.');
    return false;
  }
  return true;
  
}

  </script>
</body>
</html>