<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    die("Access denied");
}

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// POST handler: robustly accept test_db_id OR test_id and multi-select and CSV input
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    // Accept either name coming from older/newer forms
    $posted_test_id = null;
    if (isset($_POST['test_db_id'])) $posted_test_id = $_POST['test_db_id'];
    elseif (isset($_POST['test_id'])) $posted_test_id = $_POST['test_id'];

    if (empty($posted_test_id)) {
        echo "Error: no test selected.";
        exit;
    }
    $test_id = (int)$posted_test_id;

    // Collect student IDs from:
    //  - multi-select array named student_ids[] (most common)
    //  - optional text input named student_ids (CSV)
    $student_ids_raw = [];

    if (isset($_POST['student_ids']) && is_array($_POST['student_ids'])) {
        // if the text input and array share same name, PHP may coerce — handle array directly
        $student_ids_raw = $_POST['student_ids'];
    } elseif (!empty($_POST['student_ids']) && is_string($_POST['student_ids'])) {
        // could be CSV string
        $student_ids_raw = explode(',', $_POST['student_ids']);
    }

    // Also accept the multi-select as student_ids[] explicitly (some browsers send it as array)
    if (isset($_POST['student_ids']) && is_array($_POST['student_ids'])) {
        $student_ids_raw = array_merge($student_ids_raw, $_POST['student_ids']);
    }

    // If there is another field 'student_ids_text' used in UI, accept it too
    if (!empty($_POST['student_ids_text']) && is_string($_POST['student_ids_text'])) {
        $student_ids_raw = array_merge($student_ids_raw, explode(',', $_POST['student_ids_text']));
    }

    // If no students found, error
    if (empty($student_ids_raw)) {
        echo "Error: no students provided. Please select students or paste IDs.";
        exit;
    }

    // Clean, cast to int, remove invalid and duplicates
    $student_ids = [];
    foreach ($student_ids_raw as $s) {
        $s = trim((string)$s);
        if ($s === '') continue;
        // allow if numeric (string like "3") or numeric-looking, otherwise skip
        if (!ctype_digit($s)) {
            // skip non-numeric entries (you could additionally try to lookup by user_id/email if desired)
            continue;
        }
        $id = (int)$s;
        if ($id > 0) $student_ids[] = $id;
    }
    $student_ids = array_values(array_unique($student_ids));

    if (empty($student_ids)) {
        echo "Error: no valid numeric student IDs found.";
        exit;
    }

    $assigned_by = (int)$_SESSION['user_id'];

    // Start transaction and insert, skipping already-assigned entries
    $conn->begin_transaction();
    try {
        // prepare check & insert statements
        $checkStmt = $conn->prepare("SELECT COUNT(*) AS c FROM test_eligibility WHERE test_id = ? AND student_id = ?");
        if (!$checkStmt) throw new Exception("Prepare failed (check): " . $conn->error);

        $insertStmt = $conn->prepare("INSERT INTO test_eligibility (test_id, student_id, assigned_by) VALUES (?, ?, ?)");
        if (!$insertStmt) throw new Exception("Prepare failed (insert): " . $conn->error);

        $added = 0;
        $skipped = 0;
        foreach ($student_ids as $sid) {
            // skip if student doesn't exist (optional safety)
            $userRow = $conn->query("SELECT id FROM users WHERE id=" . (int)$sid . " LIMIT 1");
            if (!$userRow || $userRow->num_rows === 0) {
                $skipped++;
                continue;
            }

            // check existing
            $checkStmt->bind_param("ii", $test_id, $sid);
            $checkStmt->execute();
            $cnt = (int)$checkStmt->get_result()->fetch_assoc()['c'];
            if ($cnt > 0) {
                $skipped++;
                continue;
            }

            // insert
            $insertStmt->bind_param("iii", $test_id, $sid, $assigned_by);
            if (!$insertStmt->execute()) {
                throw new Exception("Insert failed for student_id {$sid}: " . $insertStmt->error);
            }
            $added++;
        }

        $conn->commit();

        echo "Assigned {$added} student(s). Skipped {$skipped} (already assigned / invalid).";
    } catch (Exception $e) {
        $conn->rollback();
        echo "Error while assigning: " . htmlspecialchars($e->getMessage());
    } finally {
        if (isset($checkStmt) && $checkStmt) $checkStmt->close();
        if (isset($insertStmt) && $insertStmt) $insertStmt->close();
    }
    exit;
}

// show simple form with student list
$students = $conn->query("SELECT id, name, email FROM users WHERE role='student' ORDER BY name");
$tests = $conn->query("SELECT id, title FROM tests ORDER BY created_at DESC");
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Assign Students — Admin</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    /* (your existing styles preserved) */
    :root{
      --bg:#f6f8fb;
      --card:#ffffff;
      --muted:#6b7280;
      --primary:#0b6ef6;
      --accent:#7c3aed;
      --radius:12px;
      --shadow: 0 12px 30px rgba(11,22,50,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    }
    *{box-sizing:border-box}
    body{ margin:0; background:linear-gradient(180deg,var(--bg),#f3f6fa); color:#071033; padding:26px; -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }

    .wrap{ max-width:1100px; margin:0 auto; display:grid; gap:18px; }

    header{ display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .back { text-decoration:none; color:var(--muted); display:inline-flex; gap:8px; align-items:center; padding:8px 12px; border-radius:10px; background:var(--card); box-shadow:var(--shadow); font-weight:700; }
    h1{ margin:0; font-size:20px; }
    .lead{ color:var(--muted); margin-top:6px; font-size:13px }

    .card { background:var(--card); border-radius:var(--radius); padding:18px; box-shadow:var(--shadow); }

    .grid { display:grid; grid-template-columns: 1fr 360px; gap:18px; align-items:start; }
    @media (max-width:980px){ .grid { grid-template-columns: 1fr; } }

    .field { display:flex; flex-direction:column; gap:8px; margin-bottom:8px; }
    label{ font-weight:700; font-size:13px; color:#0b1220; }
    .hint{ font-size:13px; color:var(--muted); }

    .search { display:flex; gap:8px; align-items:center; padding:8px; background:#fbfdff; border-radius:10px; border:1px solid #eef4ff; }
    .search input { border:0; outline:0; font-size:14px; background:transparent; width:100%; color:#071033; padding:6px; }

    select[multiple] { width:100%; min-height:100px; padding:10px; border-radius:8px; border:1px solid #e6eef8; font-size:14px; background:#fff; }
    .student-option { padding:6px 8px; font-size:14px; }

    input[type="text"]{ padding:10px; border-radius:8px; border:1px solid #e6eef8; font-size:14px; width:100%; }

    .actions { display:flex; gap:10px; align-items:center; margin-top:8px; }
    .btn { padding:10px 14px; border-radius:10px; font-weight:700; text-decoration:none; color:#fff; background:var(--primary); border:none; cursor:pointer; box-shadow:0 8px 20px rgba(11,110,246,0.12); }
    .btn.ghost { background:transparent; color:var(--primary); border:1px solid rgba(11,110,246,0.08); box-shadow:none; }

    .small{ font-size:13px; color:var(--muted); }

    .selected-count { font-weight:700; color:#0b1220; }

    .panel { display:flex; flex-direction:column; gap:12px; }

    .panel .card { padding:12px; }
    .note { font-size:13px; color:var(--muted) }

    .footer-actions { display:flex; justify-content:flex-end; gap:10px; margin-top:10px; }

    /* responsive */
    @media (max-width:640px) {
      .search { padding:6px; }
      select[multiple]{ min-height:200px; }
    }
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
        <h1 style="margin-top:10px">Assign Students to Test</h1>
        <div class="lead">Pick students and assign them to the selected test.</div>
      </div>

      <div style="display:flex;gap:8px;align-items:center">
        <a class="btn ghost" href="create_test.php">Create Test</a>
        <a class="btn" href="manage_users.php">Manage Users</a>
      </div>
    </header>

    <div class="card">
      <form id="assignForm" method="post" onsubmit="return confirmSubmit();">
        <div class="grid">
          <div>
            <div class="field">
              <label for="test_db_id">Select Test</label>
              <select id="test_db_id" name="test_db_id" required>
                <?php while($t=$tests->fetch_assoc()): ?>
                  <option value="<?= (int)$t['id'] ?>"><?= htmlspecialchars($t['title']) ?></option>
                <?php endwhile; ?>
              </select>
            </div>

            <div class="field">
              <label>Search students</label>
              <div class="search">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M21 21l-4.35-4.35" stroke="#94a3b8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#94a3b8" stroke-width="1.6"/></svg>
                <input id="studentSearch" type="search" placeholder="Search by name or email...">
              </div>
            </div>

            <div class="field">
              <label for="student_select">Select students <span class="small hint">(hold Ctrl/Cmd to multi-select)</span></label>
              <select id="student_select" name="student_ids[]" multiple size="12" required>
                <?php
                  // Rewind student result if needed (in case num_rows used earlier consumed it).
                  if ($students && $students->num_rows > 0) {
                    // fetch_assoc earlier may have been consumed, but we re-query to be safe
                    $students = $conn->query("SELECT id, name, email FROM users WHERE role='student' ORDER BY name");
                  }
                  while($s=$students->fetch_assoc()):
                ?>
                  <option class="student-option" value="<?= (int)$s['id'] ?>"><?= htmlspecialchars($s['name']." — ".$s['email']) ?></option>
                <?php endwhile; ?>
              </select>
              <div style="margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
                <div class="small note">Selected: <span id="selectedCount" class="selected-count">0</span></div>
                <div class="small note">Total students listed: <strong id="totalStudents"><?= $students ? $students->num_rows : 0 ?></strong></div>
              </div>
            </div>

            <div class="field">
              <label for="student_ids_text">Or paste comma-separated IDs (optional)</label>
              <input id="student_ids_text" name="student_ids_text" type="text" placeholder="e.g. 3,5,7">
              <div class="small hint">If both multi-select and comma input are provided, the backend supports either. (This keeps compatibility.)</div>
            </div>

            <div class="footer-actions">
              <button type="submit" class="btn">Assign Selected</button>
              <button type="button" class="btn ghost" onclick="clearSelection()">Clear Selection</button>
            </div>
          </div>

          <aside class="panel">
            <div class="card">
              <h4 style="margin:0 0 8px 0">Quick Actions</h4>
              <div style="display:flex;flex-direction:column;gap:8px">
                <a class="small" href="manage_users.php">Manage Users</a>
                <a class="small" href="create_test.php">Create Test</a>
                <a class="small" href="admin_dashboard.php">Dashboard</a>
              </div>
            </div>

            <div class="card">
              <h4 style="margin:0 0 8px 0">Tips</h4>
              <div class="small note">
                • Use the search to quickly find students.<br>
                • Use Ctrl/Cmd + Click to select multiple entries in the list.<br>
                • Pasting comma-separated IDs is useful for bulk assignments by ID.
              </div>
            </div>

            <div class="card">
              <h4 style="margin:0 0 8px 0">Preview</h4>
              <div class="small note" id="previewList">No students selected.</div>
            </div>
          </aside>
        </div>
      </form>
    </div>
  </div>

<script>
  // Elements
  const searchInput = document.getElementById('studentSearch');
  const studentSelect = document.getElementById('student_select');
  const selectedCount = document.getElementById('selectedCount');
  const previewList = document.getElementById('previewList');
  const totalStudents = document.getElementById('totalStudents');

  function updateSelectedCount(){
    const selected = Array.from(studentSelect.selectedOptions).map(o => ({id: o.value, text: o.textContent}));
    selectedCount.textContent = selected.length;
    if (selected.length === 0) {
      previewList.textContent = 'No students selected.';
    } else {
      previewList.innerHTML = selected.slice(0,10).map(s => s.text).join('<br>');
      if (selected.length > 10) previewList.innerHTML += `<div class="small" style="margin-top:6px;color:${getComputedStyle(document.documentElement).getPropertyValue('--muted')}">Showing 10 of ${selected.length} selected.</div>`;
    }
  }

  // search filter
  searchInput.addEventListener('input', (e) => {
    const q = e.target.value.trim().toLowerCase();
    const opts = Array.from(studentSelect.options);
    let visible = 0;
    opts.forEach(o => {
      const txt = (o.textContent||'').toLowerCase();
      if (q === '' || txt.includes(q)) {
        o.style.display = '';
        visible++;
      } else {
        o.style.display = 'none';
      }
    });
    // preserve selection counts
    updateSelectedCount();
  });

  // update selected count when selection changes
  studentSelect.addEventListener('change', updateSelectedCount);
  // initialize
  updateSelectedCount();

  // clear selection helper
  function clearSelection() {
    Array.from(studentSelect.options).forEach(o => o.selected = false);
    document.getElementById('student_ids_text').value = '';
    updateSelectedCount();
  }

  // confirm on submit
  function confirmSubmit(){
    // determine which test id field name to send (we use test_db_id from the select)
    // make sure there's a hidden input with name 'test_id' as well to support older handlers
    const select = document.getElementById('test_db_id');
    const existing = document.querySelector('input[name="test_id"]');
    if (!existing) {
      const h = document.createElement('input');
      h.type = 'hidden'; h.name = 'test_id'; h.value = select.value;
      document.getElementById('assignForm').appendChild(h);
    } else {
      existing.value = select.value;
    }

    // if no selection and no text, block
    const multi = Array.from(studentSelect.selectedOptions).map(o => o.value);
    const text = document.getElementById('student_ids_text').value.trim();
    if (multi.length === 0 && text === '') {
      alert('Please select at least one student (or paste comma-separated IDs).');
      return false;
    }

    // show a quick confirmation summary
    const count = multi.length || (text ? text.split(',').filter(s => s.trim() !== '').length : 0);
    return confirm(`Are you sure you want to assign ${count} student(s) to the selected test?`);
  }
</script>
</body>
</html>
