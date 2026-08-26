<?php
session_start();
if (!isset($_SESSION['user_id'])) die("Login required");
$uid = $_SESSION['user_id'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if (!$test_id) die("Invalid test");

// verify eligibility
$stmt = $conn->prepare("SELECT * FROM test_eligibility WHERE test_id=? AND student_id=?");
$stmt->bind_param("ii", $test_id, $uid);
$stmt->execute();
$stmt->store_result();
if ($stmt->num_rows === 0) die("You are not eligible for this test");

// load test
$stmt2 = $conn->prepare("SELECT * FROM tests WHERE id=? LIMIT 1");
$stmt2->bind_param("i", $test_id);
$stmt2->execute();
$tres = $stmt2->get_result()->fetch_assoc();
$duration = (int)$tres['duration_minutes'];

// load questions
$qstmt = $conn->prepare("SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE test_id=?");
$qstmt->bind_param("i",$test_id);
$qstmt->execute();
$qres = $qstmt->get_result();
$questions = [];
while($r = $qres->fetch_assoc()) $questions[] = $r;

// create attempt token (production: persist in DB linked to user/test)
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

      <!-- consent overlay -->
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
/* UPDATED proctor client script
   Replace the previous client-side proctoring script with this block.
   Behavior:
   - examEnded flag prevents disqualification after user legitimately finishes.
   - fullscreen exit disqualifies only while examEnded === false.
*/

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
const micStatus = document.getElementById('micStatus');
const fsStatus  = document.getElementById('fsStatus');
const examArea = document.getElementById('examArea');
const timerEl = document.getElementById('timer');
const submitBtn = document.getElementById('submitBtn');

const attemptToken = document.querySelector('input[name="attempt_token"]').value;
const testId = document.querySelector('input[name="test_id"]').value;

let sharedStream = null;
let mediaRecorder = null;
let isRecording = false;
let timerInterval = null;
let testDuration = <?= $duration ?> * 60; // seconds
let testEndAt = null;

// IMPORTANT: flag set true when test is finished via submit/timeup/finalize
let examEnded = false;
// prevent multiple terminate attempts
let terminateCalled = false;


async function postJSON(url, obj) {
  return fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(obj)
  }).catch(e => { console.warn('postJSON failed', e); throw e; });
}

async function uploadChunk(blob, index, isLast=0) {
  const fd = new FormData();
  fd.append('attempt_token', attemptToken);
  fd.append('test_id', testId);
  fd.append('chunkIndex', index);
  fd.append('isLast', isLast);
  fd.append('chunk', blob, `chunk_${index}.webm`);
  try {
    const res = await fetch(UPLOAD_URL, { method:'POST', body: fd, credentials: 'include' });
    return res.json().catch(()=>({ok: res.ok}));
  } catch(e) {
    console.error('uploadChunk error', e);
    return { ok: 0, error: String(e) };
  }
}

async function finalizeRecording(filename, duration_seconds) {
  const fd = new FormData();
  fd.append('attempt_token', attemptToken);
  fd.append('test_id', testId);
  fd.append('filename', filename);
  fd.append('duration_seconds', duration_seconds);
  try {
    const res = await fetch(FINALIZE_URL, { method:'POST', body: fd, credentials: 'include' });
    return res.json().catch(()=>({ok: res.ok}));
  } catch(e) {
    console.error('finalize error', e);
    return { ok: 0, error: String(e) };
  }
}

async function terminateAttempt(reason='fullscreen_exit') {
  if (examEnded) {
    console.log('terminateAttempt skipped because examEnded==true');
    return;
  }
  if (terminateCalled) {
    console.log('terminateAttempt already called, ignoring');
    return;
  }
  terminateCalled = true;
  try {
    await postJSON(TERMINATE_URL, { attempt_token: attemptToken, test_id: testId, reason, ts: new Date().toISOString() });
  } catch(e) {
    console.warn('terminateAttempt POST failed', e);
  }
  // stop recording & tracks
  try { if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop(); } catch(e){}
  try { if (sharedStream) sharedStream.getTracks().forEach(t=>t.stop()); } catch(e){}
  // mark exam ended to avoid multiple redirects
  examEnded = true;
  alert('You exited fullscreen or violated the policy. Your attempt has been terminated and you are disqualified.');
  window.location.href = 'disqualified.php';
}

async function obtainMedia() {
  camStatus.textContent = 'Requesting...';
  micStatus.textContent = 'Requesting...';
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    camStatus.textContent = 'Not supported';
    micStatus.textContent = 'Not supported';
    throw new Error('getUserMedia not supported');
  }
  try {
    const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: true });
    sharedStream = s;
    consentCamPreview.srcObject = s;
    camStatus.textContent = 'Allowed';
    micStatus.textContent = 'Allowed';
    return s;
  } catch(e) {
    camStatus.textContent = 'Denied/Error';
    micStatus.textContent = 'Denied/Error';
    throw e;
  }
}

async function requestFullscreen() {
  fsStatus.textContent = 'Requesting...';
  try {
    const el = document.documentElement;
    if (el.requestFullscreen) await el.requestFullscreen();
    else if (el.webkitRequestFullscreen) await el.webkitRequestFullscreen();
    fsStatus.textContent = 'Entered';
    return true;
  } catch(e) {
    fsStatus.textContent = 'Denied';
    throw e;
  }
}

async function startRecording() {
  if (!sharedStream) throw new Error('No media stream');
  camPreview.style.display = 'block';
  camPreview.srcObject = sharedStream;
  try {
    mediaRecorder = new MediaRecorder(sharedStream, { mimeType: 'video/webm;codecs=vp8,opus' });
  } catch(e) {
    mediaRecorder = new MediaRecorder(sharedStream);
  }

  let chunkIndex = 0;
  let startTs = Date.now();
  isRecording = true;

  mediaRecorder.ondataavailable = function(e) {
    if (!e.data || e.data.size === 0) return;
    // do not upload if examEnded (safety)
    if (!examEnded) uploadChunk(e.data, chunkIndex, 0).catch(()=>{});
    chunkIndex++;
  };

  mediaRecorder.onstop = async function() {
    isRecording = false;
    const duration_seconds = Math.round((Date.now() - startTs) / 1000);
    const filename = `rec_${attemptToken}_${new Date().toISOString().replace(/[:.]/g,'-')}.webm`;
    // Only finalize if exam not terminated due to fullscreen exit. If terminateAttempt already called it may still be OK.
    try {
      await finalizeRecording(filename, duration_seconds);
    } catch(err) {
      console.warn('finalizeRecording failed', err);
    }
  };

  mediaRecorder.start(CHUNK_MS);
  console.log('[record] started');
}

async function stopRecordingAndFinalize() {
  // Set examEnded early to avoid terminate on fullscreen exit during finalize
  examEnded = true;
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    if (typeof mediaRecorder.requestData === 'function') mediaRecorder.requestData();
    mediaRecorder.stop();
  }
  if (sharedStream) {
    sharedStream.getTracks().forEach(t=>t.stop());
    sharedStream = null;
  }
}

// Timer and submit logic
function startTimer() {
  testEndAt = Date.now() + testDuration * 1000;
  timerEl.textContent = formatTime(testDuration);
  timerInterval = setInterval(()=>{
    const rem = Math.max(0, Math.floor((testEndAt - Date.now())/1000));
    timerEl.textContent = formatTime(rem);
    if (rem <= 0) {
      clearInterval(timerInterval);
      // mark ended then submit
      examEnded = true; // auto-submitted due to time
      alert('Time is up. Submitting...');
      submitExam();
    }
  }, 500);
}
function formatTime(sec) {
  const m = Math.floor(sec/60), s = sec%60;
  return m + ':' + (s<10?'0'+s:s);
}

async function submitExam() {
  if (examEnded) {
    // already in end flow, do nothing more
    return;
  }
  examEnded = true;      // prevent further disqualifications
  try {
    await stopRecordingAndFinalize();
  } catch(e){ console.warn('stopRecordingAndFinalize err', e); }
  // small delay for finalize to register on server
  setTimeout(()=> {
    document.getElementById('answersForm').submit();
  }, 900);
}

// Consent start click
consentStart.addEventListener('click', async function(){
  consentStart.disabled = true;
  consentCancel.disabled = true;
  try {
    await obtainMedia();
    await requestFullscreen();
    // hide overlay and show exam
    // hide overlay and show exam
    consentOverlay.style.display = 'none';
    examArea.style.display = 'block';
    // start recording and timer
    await startRecording();
    // start the face monitor (make sure face-api models are hosted at MODEL_PATH)
    if (window.FaceMonitor && typeof window.FaceMonitor.start === 'function') {
      try { window.FaceMonitor.start(); } catch(err) { console.warn('FaceMonitor.start() error', err); }
    }
    startTimer();

    // fullscreen exit detection — only disqualify while exam is active
    document.addEventListener('fullscreenchange', () => {
      if (!document.fullscreenElement) {
        // if exam already ended, ignore; else terminate
        if (!examEnded) terminateAttempt('fullscreen_exit');
      } else {
        // user re-entered fullscreen; just update status
        fsStatus.textContent = 'Entered';
      }
    });

  } catch (err) {
    console.error('Start failed', err);
    alert('Could not start exam. Please allow camera & microphone and allow fullscreen. Try again.');
    consentStart.disabled = false;
    consentCancel.disabled = false;
  }
});

consentCancel.addEventListener('click', function(){ window.location.href = 'student_dashboard.php'; });
submitBtn.addEventListener('click', function(){ if (confirm('Submit test now?')) submitExam(); });

// Prevent normal submit when recording active — ensure finalize runs
document.getElementById('answersForm').addEventListener('submit', function(e){
  if (!examEnded) {
    e.preventDefault(); // block direct submit
    submitExam();
  }
});

// unload cleanup
window.addEventListener('beforeunload', function(){ try{ if (sharedStream) sharedStream.getTracks().forEach(t=>t.stop()); } catch(e){} });

</script>

<!-- TensorFlow.js core -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.11.0/dist/tf.min.js"></script>

<!-- BlazeFace model -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow-models/blazeface"></script>

<!-- then your own script that calls FaceMonitor -->




<script>
/* BlazeFace Face Monitor — uses TF.js + @tensorflow-models/blazeface
   - No local .bin hosting required (model fetched from CDN).
   - Keeps same behavior: consecutiveMultiple, consecutiveAbsent, warnings UI.
*/
const BF_DETECT_INTERVAL_MS = 2000;
const BF_MULTIPLE_LIMIT = 3;
const BF_ABSENT_LIMIT = 5;
const BF_SHOW_UI_WARNINGS = true;

let bfMonitor = {
  running: false,
  timer: null,
  consecutiveMultiple: 0,
  consecutiveAbsent: 0,
  totalWarnings: 0,
  model: null
};

const bfVideo = document.getElementById('camPreview');

if (!bfVideo) {
  console.warn('BlazeFace monitor: camPreview element not found — aborting.');
} else {
  let warnBox = null;
  if (BF_SHOW_UI_WARNINGS) {
    warnBox = document.createElement('div');
    warnBox.id = 'bf-monitor-warn';
    Object.assign(warnBox.style, {
      position:'fixed', right:'12px', bottom:'150px', zIndex:999999,
      background:'rgba(255,200,0,0.95)', color:'#000', padding:'8px 10px',
      borderRadius:'8px', fontWeight:'700', display:'none', boxShadow:'0 8px 20px rgba(0,0,0,0.12)'
    });
    warnBox.textContent = 'Proctor warnings: 0';
    document.body.appendChild(warnBox);
  }

  async function loadBlazeFaceModel() {
    if (bfMonitor.model) return bfMonitor.model;
    try {
      bfMonitor.model = await blazeface.load();
      console.log('[BlazeFace] model loaded');
      return bfMonitor.model;
    } catch (e) {
      console.error('[BlazeFace] load error', e);
      throw e;
    }
  }

  function updateWarnUI(msg) {
    if (!warnBox) return;
    if (!msg && bfMonitor.totalWarnings === 0) { warnBox.style.display = 'none'; return; }
    warnBox.style.display = 'block';
    warnBox.textContent = msg ? `Warning: ${msg}` : `Proctor warnings: ${bfMonitor.totalWarnings}`;
  }

  async function waitForVideoPlaying(timeout = 7000) {
    return new Promise((resolve, reject) => {
      if (bfVideo.readyState >= 2 && !bfVideo.paused) return resolve();
      const onPlay = () => { cleanup(); resolve(); };
      const onErr  = (e) => { cleanup(); reject(e || new Error('video failed to play')); };
      const to = setTimeout(() => { cleanup(); reject(new Error('video play timeout')); }, timeout);
      function cleanup() {
        bfVideo.removeEventListener('playing', onPlay);
        bfVideo.removeEventListener('error', onErr);
        clearTimeout(to);
      }
      bfVideo.addEventListener('playing', onPlay);
      bfVideo.addEventListener('error', onErr);
      try { bfVideo.play().catch(()=>{}); } catch(e) {}
    });
  }

  async function detectOnce() {
    if (typeof examEnded !== 'undefined' && examEnded) { stopBlazeMonitor(); return; }
    if (!bfVideo || bfVideo.readyState < 2) {
      bfMonitor.consecutiveAbsent++;
      bfMonitor.totalWarnings++;
      updateWarnUI(`No video ready (${bfMonitor.consecutiveAbsent})`);
      checkTerminate();
      return;
    }
    try {
      const model = await loadBlazeFaceModel();
      // estimate faces
      // maxFaces default: 5 — adjust if you want fewer/more
      const returnTensors = false;
      const predictions = await model.estimateFaces(bfVideo, returnTensors);
      const count = Array.isArray(predictions) ? predictions.length : 0;

      if (count === 1) {
        bfMonitor.consecutiveMultiple = 0;
        bfMonitor.consecutiveAbsent = 0;
        updateWarnUI();
      } else if (count === 0) {
        bfMonitor.consecutiveAbsent++;
        bfMonitor.totalWarnings++;
        updateWarnUI(`No face (${bfMonitor.consecutiveAbsent})`);
      } else {
        bfMonitor.consecutiveMultiple++;
        bfMonitor.totalWarnings++;
        updateWarnUI(`Multiple faces (${count}) — ${bfMonitor.consecutiveMultiple}/${BF_MULTIPLE_LIMIT}`);
      }
      checkTerminate();
    } catch (err) {
      console.error('[BlazeFace] detect error', err);
      // don't escalate to terminate immediately — show warning
      bfMonitor.totalWarnings++;
      updateWarnUI('Detection error, check console');
    }
  }

  function checkTerminate() {
    if (typeof examEnded !== 'undefined' && examEnded) { stopBlazeMonitor(); return; }
    if (bfMonitor.consecutiveMultiple >= BF_MULTIPLE_LIMIT) {
      console.warn('[BlazeFace] multiple faces — terminating');
      try { terminateAttempt('multiple_faces_detected'); } catch(e){
        fetch('/proctor/terminate_attempt.php', {
          method:'POST', credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ attempt_token: attemptToken, test_id: testId, reason:'multiple_faces_detected'})
        }).finally(()=> window.location.href = 'disqualified.php?reason=multiple_faces_detected&test_id=' + encodeURIComponent(testId));
      }
      stopBlazeMonitor();
    }
    if (bfMonitor.consecutiveAbsent >= BF_ABSENT_LIMIT) {
      console.warn('[BlazeFace] face absent — terminating');
      try { terminateAttempt('face_absent'); } catch(e){
        fetch('/proctor/terminate_attempt.php', {
          method:'POST', credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ attempt_token: attemptToken, test_id: testId, reason:'face_absent'})
        }).finally(()=> window.location.href = 'disqualified.php?reason=face_absent&test_id=' + encodeURIComponent(testId));
      }
      stopBlazeMonitor();
    }
  }

  async function startBlazeMonitor() {
    if (bfMonitor.running) return;
    bfMonitor.running = true;
    bfMonitor.consecutiveMultiple = 0;
    bfMonitor.consecutiveAbsent = 0;
    bfMonitor.totalWarnings = 0;
    try {
      await loadBlazeFaceModel();
      await waitForVideoPlaying(7000);
      // small warm-up
      await new Promise(r => setTimeout(r, 300));
      bfMonitor.timer = setInterval(detectOnce, BF_DETECT_INTERVAL_MS);
      detectOnce();
      console.log('[BlazeFace] monitor started');
    } catch (e) {
      console.error('[BlazeFace] could not start', e);
      updateWarnUI('Face monitor failed to start — check console for errors');
      bfMonitor.running = false;
    }
  }

  function stopBlazeMonitor() {
    bfMonitor.running = false;
    if (bfMonitor.timer) { clearInterval(bfMonitor.timer); bfMonitor.timer = null; }
    if (warnBox) warnBox.style.display = 'none';
    console.log('[BlazeFace] monitor stopped');
  }

  // expose same API so main script can call FaceMonitor.start()
  window.FaceMonitor = {
    start: startBlazeMonitor,
    stop: stopBlazeMonitor,
    getState: () => ({ ...bfMonitor })
  };
}

</script>


</body>
</html>
