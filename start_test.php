<?php
session_start();
if (!isset($_SESSION['user_id'])) die("Login required");
$uid = $_SESSION['user_id'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if (!$test_id) die("Invalid test");

$attempt_token = bin2hex(random_bytes(12));


$stmt = $conn->prepare("SELECT * FROM test_eligibility WHERE test_id=? AND student_id=?");
$stmt->bind_param("ii", $test_id, $uid);
$stmt->execute();
$stmt->store_result();
if ($stmt->num_rows === 0) die("You are not eligible for this test");


$stmt2 = $conn->prepare("SELECT * FROM tests WHERE id=? LIMIT 1");
$stmt2->bind_param("i", $test_id);
$stmt2->execute();
$tres = $stmt2->get_result()->fetch_assoc();
$duration = (int)$tres['duration_minutes'];

$qstmt = $conn->prepare("SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE test_id=?");
$qstmt->bind_param("i",$test_id);
$qstmt->execute();
$qres = $qstmt->get_result();
$questions = [];
while($r = $qres->fetch_assoc()) $questions[] = $r;
$attempt_token = bin2hex(random_bytes(12));
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title><?=htmlspecialchars($tres['title'])?> — Proctored Test</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">

<!-- TensorFlow.js core -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.11.0/dist/tf.min.js"></script>

<!-- BlazeFace model -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow-models/blazeface"></script>

<!-- then your own script that calls FaceMonitor -->
    <style>
    body{font-family:Inter,system-ui,Arial;margin:0;background:#f5f7fb;color:#071033}
    .wrap{max-width:1000px;margin:18px auto;padding:18px}
    .card{background:#fff;border-radius:10px;padding:16px;box-shadow:0 8px 24px rgba(6,15,34,0.06)}
    h1{margin:0 0 6px 0;font-size:20px}
    .meta{color:#6b7280;font-size:13px;margin-bottom:12px}
    #consentOverlay{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(2,6,23,0.6);z-index:9999}
    #consentBox{width:92%;max-width:760px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 18px 50px rgba(2,6,23,0.35)}
    .btn{cursor:pointer;padding:10px 14px;border-radius:8px;border:0;font-weight:700}
    .btn.primary{background:#0b6ef6;color:#fff}
    .btn.ghost{background:transparent;color:#0b6ef6;border:1px solid rgba(11,110,246,0.12)}
    #consentCamPreview{width:160px;height:120px;background:#000;border-radius:8px;object-fit:cover}
    #camPreview{display:none;position:fixed;right:12px;top:12px;width:160px;height:120px;border-radius:8px;z-index:9998;box-shadow:0 10px 30px rgba(2,6,23,0.2)}
    #consentCamPreview{width:160px;height:120px;background:#000;border-radius:8px;object-fit:cover}
    #camPreview{display:none;position:fixed;right:12px;top:12px;width:160px;height:120px;border-radius:8px;z-index:9998;box-shadow:0 10px 30px rgba(2,6,23,0.2)}
    .muted{color:#6b7280;font-size:13px}
    /* hide exam UI until started */
    #examArea{display:none}
    .qcard{border-radius:10px;padding:14px;background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:0 6px 20px rgba(11,22,50,0.04);margin-bottom:12px}
    .options{display:grid;gap:8px;margin-top:12px}
    label.opt{display:flex;gap:8px;align-items:flex-start;padding:10px;border-radius:8px;border:1px solid #e7eefb;background:#fff;cursor:pointer}
    .timer{font-weight:800;color:#ef4444;font-size:18px}
    .hidden{display:none}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1><?=htmlspecialchars($tres['title'])?></h1>
      <div class="meta">Duration: <strong><?= (int)$duration ?> minutes</strong></div>
      <p class="muted">This test is proctored. You must allow camera & microphone and stay in fullscreen. Exiting fullscreen will disqualify you.</p>
      <div id="consentOverlay" role="dialog" aria-modal="true">
        <div id="consentBox">
          <h2>Start proctored test</h2>
          <p class="muted">We will record your camera and microphone for exam integrity. The browser will ask permission. You must enter fullscreen. Exiting fullscreen will end your attempt and disqualify you.</p>
          <div style="display:flex;gap:12px;align-items:center;margin-top:12px">
            <video id="consentCamPreview" autoplay muted playsinline></video>
            <div style="flex:1">
              <div><strong>Camera:</strong> <span id="camStatus">Not started</span></div>
              <div><strong>Microphone:</strong> <span id="micStatus">Not started</span></div>
              <div><strong>Fullscreen:</strong> <span id="fsStatus">Not entered</span></div>
              </div>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
            <button id="consentCancel" class="btn ghost" type="button">Cancel</button>
            <button id="consentStart" class="btn primary" type="button">Start exam (I consent)</button>
          </div>
        </div>
      </div>
      <!-- floating camera preview shown during test -->
      <video id="camPreview" autoplay muted playsinline></video>
      <!-- EXAM AREA (hidden until consent) -->
      <div id="examArea" style="margin-top:16px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong>Time left:</strong> <span id="timer" class="timer">--:--</span></div>
          <div>
            <button id="submitBtn" class="btn primary">Submit (Stop & Upload)</button>
          </div>
        </div>
        <form id="answersForm" method="post" action="submit_answers.php">
          <input type="hidden" name="test_id" value="<?= $test_id ?>">
          <input type="hidden" name="attempt_token" value="<?= htmlspecialchars($attempt_token) ?>">
          <div id="questionsWrap" style="margin-top:12px">
            <?php foreach($questions as $idx => $q): ?>
              <div class="qcard" data-qid="<?= (int)$q['id'] ?>" data-idx="<?= $idx ?>">
                <div><strong>Q<?= $idx + 1 ?>.</strong></div>
                <div style="margin-top:8px"><?= htmlspecialchars($q['question_text']) ?></div>
                <div class="options" role="radiogroup">
                  <label class="opt"><input type="radio" name="q_<?= (int)$q['id'] ?>" value="a"> <span>A — <?= htmlspecialchars($q['option_a']) ?></span></label>
                  <label class="opt"><input type="radio" name="q_<?= (int)$q['id'] ?>" value="b"> <span>B — <?= htmlspecialchars($q['option_b']) ?></span></label>
                  <label class="opt"><input type="radio" name="q_<?= (int)$q['id'] ?>" value="c"> <span>C — <?= htmlspecialchars($q['option_c']) ?></span></label>
                  <label class="opt"><input type="radio" name="q_<?= (int)$q['id'] ?>" value="d"> <span>D — <?= htmlspecialchars($q['option_d']) ?></span></label>
                </div>
              </div>
            <?php endforeach; ?>
          </div>
        </form>
      </div>

    </div>
  </div>
  <script>
const UPLOAD_URL = '/proctor/upload_recording.php';
const FINALIZE_URL = '/proctor/finalize_recording.php';
const TERMINATE_URL = '/proctor/terminate_attempt.php';
const CHUNK_MS = 5000;

const consentOverlay = document.getElementById('consentOverlay');
const consentStart = document.getElementById('consentStart');
const consentCancel = document.getElementById('consentCancel');
const consentCamPreview = document.getElementById('consentCamPreview');
const camPreview = document.getElementById('camPreview');
const camStatus = document.getElementById('camStatus');
const fsStatus  = document.getElementById('fsStatus');
const examArea = document.getElementById('examArea');
const timerEl = document.getElementById('timer');
const submitBtn = document.getElementById('submitBtn');


const attemptToken = document.querySelector('input[name="attempt_token"]').value;
const testId = document.querySelector('input[name="test_id"]').value;

let sharedStream = null;
let mediaRecorder = null;
let isRecording = false;



</script>


<script>


</script>


</body>
</html>