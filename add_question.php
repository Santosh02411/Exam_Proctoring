<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') die("Access denied");

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_db_id = isset($_GET['test_db_id']) ? (int)$_GET['test_db_id'] : null;
if (!$test_db_id) {
    echo "Provide test_db_id";
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $qtext = $_POST['question_text'];
    $a = $_POST['option_a'];
    $b = $_POST['option_b'];
    $c = $_POST['option_c'];
    $d = $_POST['option_d'];
    $correct = $_POST['correct_answer'];
    $marks = (int)$_POST['marks'];

    $stmt = $conn->prepare("INSERT INTO questions (test_id, question_text, option_a, option_b, option_c, option_d, correct_answer, marks) VALUES (?, ?, ?, ?, ?, ?, ?, ?)");
    $stmt->bind_param("issssssi", $test_db_id, $qtext, $a, $b, $c, $d, $correct, $marks);
    if ($stmt->execute()) echo "Question added.<br>";
    else echo "Err: ".$stmt->error;
}
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
  <title>Add Question — Test <?= (int)$test_db_id ?></title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f6f8fb;
      --card:#ffffff;
      --muted:#6b7280;
      --primary:#0b6ef6;
      --accent:#7c3aed;
      --danger:#ef4444;
      --radius:12px;
      --shadow: 0 10px 30px rgba(11,22,50,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
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
         <h1 style="margin-top:10px">Add Question</h1>
        <div class="lead">Test ID: <strong><?= (int)$test_db_id ?></strong> — Add MCQs for this test</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <a class="btn ghost" href="view_test.php?test_id=<?= (int)$test_db_id ?>">View Test</a>
        <a class="btn" href="create_test.php">Create Test</a>
      </div>
      </header>
      <div class="card">
      <form class="grid" method="post" id="qForm" onsubmit="return validateAndSubmit();">
        <div>
          <div class="field">
            <label for="question_text">Question text</label>
            <textarea id="question_text" name="question_text" placeholder="Write the question here..." required></textarea>
          </div>
          <div class="row">
            <div class="col field">
              <label for="option_a">Option A</label>
              <input id="option_a" name="option_a" type="text" placeholder="Option A" required>
            </div>
            <div class="col field">
              <label for="option_b">Option B</label>
              <input id="option_b" name="option_b" type="text" placeholder="Option B" required>
            </div>
          </div>
          <div class="row">
            <div class="col field">
              <label for="option_c">Option C</label>
              <input id="option_c" name="option_c" type="text" placeholder="Option C" required>
            </div>
            <div class="col field">
              <label for="option_d">Option D</label>
              <input id="option_d" name="option_d" type="text" placeholder="Option D" required>
            </div>
          </div>
          <div class="row">
            <div class="col field">
              <label for="correct_answer">Correct answer</label>
              <select id="correct_answer" name="correct_answer" required>
                <option value="a">A</option>
                <option value="b">B</option>
                <option value="c">C</option>
                <option value="d">D</option>
              </select>
            </div>
            <div class="col field">
              <label for="marks">Marks</label>
              <input id="marks" name="marks" type="number" min="1" value="1" required>
            </div>
          </div>
          <div class="actions">
            <button type="submit" class="btn">Add Question</button>
            <button type="button" class="btn ghost" onclick="clearForm()">Clear</button>
            <a class="btn ghost" href="view_test.php?test_id=<?= (int)$test_db_id ?>">Done</a>
          </div>
           <div id="msg" style="margin-top:12px"></div>
        </div>
        <!-- Preview / help column -->
        <aside class="preview">
          <div class="qcard" aria-live="polite">
            <div class="qno">Preview</div>
            <div class="qtext" id="pv_q">Your question preview will appear here as you type.</div>
            <div class="opts" id="pv_opts">
              <div class="opt" id="pv_a">A — Option A</div>
              <div class="opt" id="pv_b">B — Option B</div>
              <div class="opt" id="pv_c">C — Option C</div>
              <div class="opt" id="pv_d">D — Option D</div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;">
              <div class="small">Correct: <strong id="pv_correct">—</strong></div>
              <div class="small">Marks: <strong id="pv_marks">1</strong></div>
            </div>
          </div>
          div class="card" style="padding:12px">
            <h4 style="margin:0 0 8px 0">Tips</h4>
             <p class="small" style="margin:0">
              • Keep question short and clear.<br>
              • Ensure only one correct option is selected.<br>
              • Use marks &amp; consistent styling for all questions.
            </p>
          </div>
          div class="card" style="padding:12px">
            <h4 style="margin:0 0 8px 0">Quick Links</h4>
            <div style="display:flex;flex-direction:column;gap:8px">
              <a class="small" href="view_test.php?test_id=<?= (int)$test_db_id ?>">View Test</a>
              <a class="small" href="assign_students.php?test_id=<?= (int)$test_db_id ?>">Assign Students</a>
              <a class="small" href="admin_dashboard.php">Dashboard</a>
            </div>
             </div>
        </aside>
      </form>
    </div>
    </div>
  </div>
<script>
  // Live preview bindings
  const qText = document.getElementById('question_text');
  const aEl = document.getElementById('option_a');
  const bEl = document.getElementById('option_b');
  const cEl = document.getElementById('option_c');
  const dEl = document.getElementById('option_d');
  const correctEl = document.getElementById('correct_answer');
  const marksEl = document.getElementById('marks');

  const pv_q = document.getElementById('pv_q');
  const pv_a = document.getElementById('pv_a');
  const pv_b = document.getElementById('pv_b');
  const pv_c = document.getElementById('pv_c');
  const pv_d = document.getElementById('pv_d');
  const pv_correct = document.getElementById('pv_correct');
  const pv_marks = document.getElementById('pv_marks');
  const msg = document.getElementById('msg');
  function safe(s){ return (s||'').toString().replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function updatePreview(){
     pv_q.innerHTML = safe(qText.value) || 'Your question preview will appear here as you type.';
    pv_a.innerText = 'A — ' + (aEl.value || 'Option A');
    pv_b.innerText = 'B — ' + (bEl.value || 'Option B');
    pv_c.innerText = 'C — ' + (cEl.value || 'Option C');
    pv_d.innerText = 'D — ' + (dEl.value || 'Option D');
    pv_marks.innerText = safe(marksEl.value || '1');
    pv_correct.innerText = safe(correctEl.value ? correctEl.value.toUpperCase() : '—');


    [pv_a,pv_b,pv_c,pv_d].forEach(el => el.classList.remove('correct'));
    if (correctEl.value === 'a') pv_a.classList.add('correct');
    if (correctEl.value === 'b') pv_b.classList.add('correct');
    if (correctEl.value === 'c') pv_c.classList.add('correct');
    if (correctEl.value === 'd') pv_d.classList.add('correct');
  }
  
  [qText,aEl,bEl,cEl,dEl,correctEl,marksEl].forEach(el => el.addEventListener('input', updatePreview));
  updatePreview();

   function clearForm(){
    qText.value=''; aEl.value=''; bEl.value=''; cEl.value=''; dEl.value=''; correctEl.value='a'; marksEl.value='1';
    updatePreview();
    msg.innerHTML = '';
  }
  function validateAndSubmit(){
    // Basic client-side validation
    if (!qText.value.trim()){
      alert('Please enter the question text.');
      qText.focus();
      return false;
    }

  }










