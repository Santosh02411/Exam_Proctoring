<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html"); exit;
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (isset($_POST['delete_test_id'])) {
        $did = (int)$_POST['delete_test_id'];
        // delete test (cascade should remove questions if FK set)
        $dstmt = $conn->prepare("DELETE FROM tests WHERE id = ?");
        $dstmt->bind_param("i", $did);
        if ($dstmt->execute()) {
            $message = "Test deleted.";
        } else {
            $message = "Unable to delete test: " . $dstmt->error;
        }
        $dstmt->close();
    }
    if (isset($_POST['toggle_test_id'])) {
        $tid = (int)$_POST['toggle_test_id'];
        // get current status
        $row = $conn->query("SELECT status FROM tests WHERE id=".$tid)->fetch_assoc();
        $new = ($row && $row['status'] === 'published') ? 'draft' : 'published';
        $ust = $conn->prepare("UPDATE tests SET status = ? WHERE id = ?");
        $ust->bind_param("si", $new, $tid);
        if ($ust->execute()) {
            $message = "Test status updated to {$new}.";
        } else {
            $message = "Unable to update status: " . $ust->error;
        }
        $ust->close();
    }
    if (isset($_POST['duplicate_test_id'])) {
        $dup = (int)$_POST['duplicate_test_id'];
        // duplicate basic test row (not copying questions to keep simple)
        $orig = $conn->query("SELECT test_id, title, description, duration_minutes, total_questions, passing_marks, status FROM tests WHERE id=".$dup)->fetch_assoc();
        if ($orig) {
            $new_code = $orig['test_id'] . '_COPY_' . time();
            $ist = $conn->prepare("INSERT INTO tests (test_id, title, description, duration_minutes, total_questions, passing_marks, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)");
            $created_by = $_SESSION['user_id'];
            $ist->bind_param("sssiiiis", $new_code, $orig['title'], $orig['description'], $orig['duration_minutes'], $orig['total_questions'], $orig['passing_marks'], $orig['status'], $created_by);
            if ($ist->execute()) {
                $message = "Test duplicated. New test id: " . $ist->insert_id;
            } else {
                $message = "Duplicate failed: " . $ist->error;
            }
            $ist->close();
        }
    }
}
$tests = $conn->query("SELECT id, test_id, title, status, duration_minutes, total_questions, passing_marks, created_at FROM tests ORDER BY created_at DESC");

?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Manage Tests — Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">

<style>
:root{ --bg:#f6f8fb; --card:#fff; --muted:#6b7280; --primary:#0b6ef6; --success:#10b981; --danger:#ef4444; --radius:12px; --shadow:0 12px 30px rgba(11,22,50,0.06); font-family:'Inter',system-ui,Arial; }
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,var(--bg),#f3f6fa);color:#071033;padding:22px}
.wrap{max-width:1100px;margin:0 auto;display:grid;gap:16px}
.header{display:flex;justify-content:space-between;align-items:center}
.header{display:flex;justify-content:space-between;align-items:center}
card{background:var(--card);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow)}
.table{width:100%;border-collapse:collapse}
.table th,.table td{padding:12px;border-bottom:1px solid #f1f5f9;text-align:left}



</style>
</head>
<body>
    <div class="wrap">
    <div class="header">
      <div>
        <a class="back" href="admin_dashboard.php">← Dashboard</a>
        <h1 style="margin:8px 0 0 0">Manage Tests</h1>
        <div class="small">Create, edit, duplicate or delete tests</div>
      </div>
      <div style="display:flex;gap:8px">
        <a class="btn" href="create_test.php">+ New Test</a>
      </div>
    </div>
    <?php if ($message): ?><div class="message"><?= htmlspecialchars($message) ?></div><?php endif; ?>
    <div class="card">
      <?php if ($tests && $tests->num_rows > 0): ?>
        <table class="table" role="table" aria-label="Tests">
          <thead>
            <tr>
              <th style="width:48px">#</th>
              <th>Title</th>
              <th>Code</th>
              <th>Duration</th>
              <th>Qs</th>
              <th>Passing</th>
              <th>Status</th>
              <th>Created</th>
              <th style="width:260px">Actions</th>
            </tr>
          </thead>
          <tbody>
            <?php $i=1; while($t = $tests->fetch_assoc()): ?>
              <tr>
                <td><?= $i++ ?></td>
                <td style="font-weight:800"><?= htmlspecialchars($t['title']) ?></td>
                <td class="small"><?= htmlspecialchars($t['test_id']) ?></td>
                <td class="small"><?= (int)$t['duration_minutes'] ?> min</td>
                <td class="small"><?= (int)$t['total_questions'] ?></td>
                <td class="small"><?= (int)$t['passing_marks'] ?></td>
                <td>
                  <?php if ($t['status'] === 'published'): ?>
                    <span class="badge published">Published</span>
                  <?php else: ?>
                    <span class="badge draft">Draft</span>
                  <?php endif; ?>
                </td>
                <td class="small"><?= htmlspecialchars($t['created_at']) ?></td>
                <td>
                  <div class="actions">
                    <a class="btn ghost" href="edit_test.php?test_id=<?= (int)$t['id'] ?>">Edit</a>

                    <form class="form-inline" method="post" onsubmit="return confirm('Duplicate this test (questions will not be copied)?');">
                      <input type="hidden" name="duplicate_test_id" value="<?= (int)$t['id'] ?>">
                      <button type="submit" class="btn ghost">Duplicate</button>
                    </form>

                    <form class="form-inline" method="post" onsubmit="return confirm('Toggle publish/draft for this test?');">
                      <input type="hidden" name="toggle_test_id" value="<?= (int)$t['id'] ?>">
                      <button type="submit" class="btn ghost"><?= $t['status'] === 'published' ? 'Unpublish' : 'Publish' ?></button>
                  </form>

                  
                  </div>
                </td>
              </tr>
            <?php endwhile; ?>
          </tbody>
        </table>
        <?php else: ?>
        <div class="small">No tests yet. Create one to get started.</div>
      <?php endif; ?>
    </div>
  </div>
</body>
</html>

















</body> 
</html>

