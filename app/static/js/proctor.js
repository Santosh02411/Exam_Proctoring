(function () {
  const cfg = window.EXAM_CONFIG;

  const consentOverlay = document.getElementById('consentOverlay');
  const consentStart = document.getElementById('consentStart');
  const verifyIdentityBtn = document.getElementById('verifyIdentityBtn');
  const consentCamPreview = document.getElementById('consentCamPreview');
  const camPreview = document.getElementById('camPreview');
  const camStatus = document.getElementById('camStatus');
  const modelStatus = document.getElementById('modelStatus');
  const identityStatus = document.getElementById('identityStatus');
  const fsStatus = document.getElementById('fsStatus');
  const examArea = document.getElementById('examArea');
  const timerEl = document.getElementById('timer');
  const submitBtn = document.getElementById('submitBtn');
  const proctorDot = document.getElementById('proctorDot');
  const proctorStatusText = document.getElementById('proctorStatusText');
  const violationBanner = document.getElementById('violationBanner');
  const answersForm = document.getElementById('answersForm');

  let sharedStream = null;
  let examActive = false;
  let examEnded = false;
  let identityVerified = false;
  let timerInterval = null;
  let faceCheckInterval = null;
  let snapshotInterval = null;
  let identityCheckInterval = null;
  let audioCheckInterval = null;
  let secondsLeft = cfg.durationSeconds;
  let noFaceStreak = 0;
  let audioLoudStreak = 0;

  let mediaRecorder = null;
  let recordingChunkIndex = 0;

  let audioCtx = null;
  let analyser = null;
  let audioDataArray = null;

  let questionTimerIntervals = [];

  // ---------- helpers ----------
  function fmtTime(s) {
    const m = Math.floor(s / 60).toString().padStart(2, '0');
    const sec = Math.floor(s % 60).toString().padStart(2, '0');
    return `${m}:${sec}`;
  }

  function showBanner(text) {
    violationBanner.textContent = text;
    violationBanner.style.display = 'block';
    setTimeout(() => { violationBanner.style.display = 'none'; }, 4000);
  }

  function setProctorBad(bad) {
    proctorDot.classList.toggle('bad', bad);
    proctorStatusText.textContent = bad ? 'Issue detected' : 'Monitoring';
    camPreview.classList.toggle('flagged', bad);
  }

  async function reportEvent(eventType, severity, details) {
    try {
      const res = await fetch(cfg.eventUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          attempt_id: cfg.attemptId,
          event_type: eventType,
          severity: severity,
          details: details || ''
        })
      });
      const data = await res.json();
      if (severity === 'violation') {
        showBanner(`Warning: ${eventType.replace(/_/g, ' ')} (${data.violation_count} violation${data.violation_count === 1 ? '' : 's'} recorded)`);
      }
      if (data.terminated) {
        endExam('terminated');
      }
      return data;
    } catch (e) {
      console.warn('reportEvent failed', e);
    }
  }

  // ---------- camera + mic + face model setup ----------
  async function initCamera() {
    try {
      sharedStream = await navigator.mediaDevices.getUserMedia({
        video: { width: 320, height: 240 },
        audio: true
      });
      consentCamPreview.srcObject = sharedStream;
      camPreview.srcObject = sharedStream;
      camStatus.textContent = 'Ready';
      maybeEnableVerify();
    } catch (e) {
      camStatus.textContent = 'Permission denied — camera & mic are required to start.';
    }
  }

  async function initFaceModel() {
    try {
      await faceapi.nets.tinyFaceDetector.loadFromUri(cfg.modelsUrl);
      await faceapi.nets.faceLandmark68Net.loadFromUri(cfg.modelsUrl);
      await faceapi.nets.faceRecognitionNet.loadFromUri(cfg.modelsUrl);
      modelStatus.textContent = 'Ready';
      maybeEnableVerify();
    } catch (e) {
      modelStatus.textContent = 'Failed to load — proctoring model unavailable.';
      console.error(e);
    }
  }

  function maybeEnableVerify() {
    if (sharedStream && faceapi.nets.tinyFaceDetector.isLoaded && faceapi.nets.faceRecognitionNet.isLoaded) {
      if (cfg.referenceDescriptor) {
        verifyIdentityBtn.disabled = false;
      } else {
        // No enrolled reference (shouldn't normally happen — server redirects to
        // enrollment first) — allow proceeding without an identity check.
        identityStatus.textContent = 'No reference photo enrolled — skipping check';
        identityVerified = true;
        consentStart.disabled = false;
      }
    }
  }

  async function getLiveDescriptor(videoEl) {
    const result = await faceapi
      .detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions())
      .withFaceLandmarks()
      .withFaceDescriptor();
    return result ? result.descriptor : null;
  }

  async function checkIdentity(videoEl) {
    if (!cfg.referenceDescriptor) return { ok: true, skipped: true };
    const liveDescriptor = await getLiveDescriptor(videoEl);
    if (!liveDescriptor) return { ok: false, reason: 'no_face' };
    const distance = faceapi.euclideanDistance(liveDescriptor, cfg.referenceDescriptor);
    return { ok: distance <= cfg.faceMatchThreshold, distance };
  }

  verifyIdentityBtn.addEventListener('click', async () => {
    verifyIdentityBtn.disabled = true;
    identityStatus.textContent = 'Checking…';
    const result = await checkIdentity(consentCamPreview);
    if (result.ok) {
      identityStatus.textContent = 'Verified ✓';
      identityVerified = true;
      consentStart.disabled = false;
    } else if (result.reason === 'no_face') {
      identityStatus.textContent = 'No face detected — look at the camera and retry.';
      verifyIdentityBtn.disabled = false;
    } else {
      identityStatus.textContent = "Face doesn't match your enrolled photo — retry, or re-enroll from your dashboard.";
      verifyIdentityBtn.disabled = false;
      reportEvent('identity_mismatch', 'warning', `Consent-stage mismatch, distance=${result.distance.toFixed(3)}`);
    }
  });

  // ---------- exam lifecycle ----------
  async function startExam() {
    if (!identityVerified) return;

    try {
      await document.documentElement.requestFullscreen();
      fsStatus.textContent = 'Entered';
    } catch (e) {
      fsStatus.textContent = 'Could not enter fullscreen — continuing anyway.';
    }

    consentOverlay.style.display = 'none';
    examArea.style.display = 'block';
    camPreview.style.display = 'block';
    examActive = true;

    startTimer();
    startFaceMonitoring();
    startSnapshotChecks();
    startIdentityRecheck();
    startAudioMonitoring();
    startRecording();
    startPerQuestionTimers();
    startSectionTimers();
    startAutosave();
  }

  function startTimer() {
    timerEl.textContent = fmtTime(secondsLeft);
    timerInterval = setInterval(() => {
      secondsLeft -= 1;
      timerEl.textContent = fmtTime(Math.max(secondsLeft, 0));
      if (secondsLeft <= 0) {
        clearInterval(timerInterval);
        submitExam('Time expired — auto-submitted.');
      }
    }, 1000);
  }

  function startPerQuestionTimers() {
    // Optional soft pacing limit per question (Question.time_limit_seconds).
    // All questions render on one page (not a stepper), so this can't block
    // navigation — instead, once a question's clock runs out, its inputs lock
    // in place and the rest of the exam continues normally on the overall timer.
    const qcards = document.querySelectorAll('.qcard[data-time-limit]');
    qcards.forEach((card) => {
      const qid = card.dataset.qid;
      const limit = parseInt(card.dataset.timeLimit, 10);
      if (!limit || isNaN(limit)) return;

      const timerLabel = card.querySelector(`[data-qtimer="${qid}"]`);
      let remaining = limit;

      const render = () => {
        if (timerLabel) timerLabel.textContent = ` — ${fmtTime(remaining)} left for this question`;
      };
      render();

      const intervalId = setInterval(() => {
        if (!examActive || examEnded) {
          clearInterval(intervalId);
          return;
        }
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(intervalId);
          remaining = 0;
          lockQuestionCard(card, timerLabel);
        } else {
          render();
        }
      }, 1000);

      questionTimerIntervals.push(intervalId);
    });
  }

  function lockQuestionCard(card, timerLabel) {
    card.classList.add('q-locked');
    if (timerLabel) timerLabel.textContent = ' — time expired for this question';

    // Deliberately NOT using `disabled` here: disabled fields are excluded from
    // FormData on submit, which would silently drop an answer the student had
    // already picked before time ran out. readOnly + blocking further clicks
    // keeps the existing value intact while preventing further changes.
    card.querySelectorAll('input[type="text"], textarea').forEach((el) => { el.readOnly = true; });
    card.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach((el) => {
      el.addEventListener('click', (e) => { e.preventDefault(); }, true);
    });
  }

  let sectionTimerIntervals = [];

  function startSectionTimers() {
    // Optional per-section time limit (Section.duration_minutes). Like the
    // per-question timer, this can't block navigation since every section's
    // questions are already visible on the page — instead, once a section's
    // clock runs out, every question card in that section locks in place
    // (same readOnly/click-blocking approach as lockQuestionCard) and the
    // rest of the exam continues on the overall timer.
    const headers = document.querySelectorAll('.section-header[data-section-duration]');
    headers.forEach((header) => {
      const sectionId = header.dataset.sectionId;
      let remaining = parseInt(header.dataset.sectionDuration, 10);
      if (!remaining || isNaN(remaining)) return;

      const timerLabel = header.querySelector(`[data-section-timer="${sectionId}"]`);
      const cards = document.querySelectorAll(`.qcard[data-section-id="${sectionId}"]`);

      const render = () => {
        if (timerLabel) timerLabel.textContent = ` — ${fmtTime(remaining)} left for this section`;
      };
      render();

      const intervalId = setInterval(() => {
        if (!examActive || examEnded) {
          clearInterval(intervalId);
          return;
        }
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(intervalId);
          remaining = 0;
          if (timerLabel) timerLabel.textContent = ' — time expired for this section';
          cards.forEach((card) => lockQuestionCard(card, card.querySelector('[data-qtimer]')));
        } else {
          render();
        }
      }, 1000);

      sectionTimerIntervals.push(intervalId);
    });
  }

  // ---------- autosave ----------
  let autosaveInterval = null;
  let autosaveDebounce = null;
  const AUTOSAVE_INTERVAL_MS = 20000;
  const AUTOSAVE_DEBOUNCE_MS = 2000;

  async function saveAnswersNow() {
    if (!examActive || examEnded) return;
    try {
      const formData = new FormData(answersForm);
      await fetch(cfg.autosaveUrl, { method: 'POST', body: formData });
    } catch (e) {
      console.warn('autosave failed', e);
    }
  }

  function startAutosave() {
    autosaveInterval = setInterval(saveAnswersNow, AUTOSAVE_INTERVAL_MS);
    answersForm.addEventListener('change', () => {
      clearTimeout(autosaveDebounce);
      autosaveDebounce = setTimeout(saveAnswersNow, AUTOSAVE_DEBOUNCE_MS);
    });
  }

  function stopAutosave() {
    clearInterval(autosaveInterval);
    clearTimeout(autosaveDebounce);
  }

  function startFaceMonitoring() {
    faceCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      try {
        const detections = await faceapi.detectAllFaces(camPreview, new faceapi.TinyFaceDetectorOptions());
        if (detections.length === 0) {
          noFaceStreak += 1;
          setProctorBad(true);
          if (noFaceStreak >= 2) { // require 2 consecutive misses (~4s) before logging
            reportEvent('no_face', 'violation', 'No face detected in webcam frame');
            noFaceStreak = 0;
          }
        } else if (detections.length > 1) {
          setProctorBad(true);
          reportEvent('multiple_faces', 'violation', `${detections.length} faces detected in frame`);
          noFaceStreak = 0;
        } else {
          noFaceStreak = 0;
          setProctorBad(false);
        }
      } catch (e) {
        console.warn('face detection error', e);
      }
    }, 2000);
  }

  function startIdentityRecheck() {
    if (!cfg.referenceDescriptor) return;
    identityCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      try {
        const result = await checkIdentity(camPreview);
        if (!result.ok && !result.skipped && result.reason !== 'no_face') {
          reportEvent('identity_mismatch', 'violation', `In-exam mismatch, distance=${result.distance.toFixed(3)}`);
        }
      } catch (e) {
        console.warn('identity re-check error', e);
      }
    }, 25000);
  }

  function startAudioMonitoring() {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(sharedStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      audioDataArray = new Uint8Array(analyser.frequencyBinCount);
      source.connect(analyser);
    } catch (e) {
      console.warn('audio monitoring unavailable', e);
      return;
    }

    audioCheckInterval = setInterval(() => {
      if (!examActive || examEnded || !analyser) return;
      analyser.getByteTimeDomainData(audioDataArray);
      let sumSquares = 0;
      for (let i = 0; i < audioDataArray.length; i++) {
        const v = (audioDataArray[i] - 128) / 128;
        sumSquares += v * v;
      }
      const rms = Math.sqrt(sumSquares / audioDataArray.length);

      const LOUD_THRESHOLD = 0.12; // sustained talking/noise, well above room-tone/background hum
      if (rms > LOUD_THRESHOLD) {
        audioLoudStreak += 1;
        if (audioLoudStreak >= 3) { // ~9s of sustained loud audio
          reportEvent('audio_violation', 'warning', `Sustained loud audio detected (rms=${rms.toFixed(3)})`);
          audioLoudStreak = 0;
        }
      } else {
        audioLoudStreak = 0;
      }
    }, 3000);
  }

  function startSnapshotChecks() {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext('2d');

    snapshotInterval = setInterval(() => {
      if (!examActive || examEnded) return;
      try {
        ctx.drawImage(camPreview, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.6);
        fetch(cfg.snapshotUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ attempt_id: cfg.attemptId, image: dataUrl })
        }).then(r => r.json()).then(data => {
          if (data.terminated) endExam('terminated');
        }).catch(() => {});
      } catch (e) {
        console.warn('snapshot capture failed', e);
      }
    }, 15000);
  }

  function startRecording() {
    if (typeof MediaRecorder === 'undefined') {
      console.warn('MediaRecorder not supported — session will not be recorded.');
      return;
    }
    const mimeCandidates = ['video/webm;codecs=vp8,opus', 'video/webm'];
    const mimeType = mimeCandidates.find(m => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) || '';

    try {
      mediaRecorder = new MediaRecorder(sharedStream, mimeType ? { mimeType } : undefined);
    } catch (e) {
      console.warn('Could not start MediaRecorder', e);
      return;
    }

    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        uploadRecordingChunk(event.data, recordingChunkIndex);
        recordingChunkIndex += 1;
      }
    };

    mediaRecorder.start(30000); // emit a chunk every 30s
  }

  function uploadRecordingChunk(blob, index) {
    const fd = new FormData();
    fd.append('attempt_id', cfg.attemptId);
    fd.append('chunk_index', index);
    fd.append('chunk', blob, `chunk_${index}.webm`);
    fetch(cfg.recordingChunkUrl, { method: 'POST', body: fd }).catch(e => {
      console.warn('recording chunk upload failed', e);
    });
  }

  function stopAllMonitoring() {
    clearInterval(timerInterval);
    clearInterval(faceCheckInterval);
    clearInterval(snapshotInterval);
    clearInterval(identityCheckInterval);
    clearInterval(audioCheckInterval);
    questionTimerIntervals.forEach((id) => clearInterval(id));
    questionTimerIntervals = [];
    sectionTimerIntervals.forEach((id) => clearInterval(id));
    sectionTimerIntervals = [];
    stopAutosave();

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      try { mediaRecorder.stop(); } catch (e) { /* noop */ }
    }
    if (audioCtx) {
      try { audioCtx.close(); } catch (e) { /* noop */ }
    }
    if (sharedStream) {
      sharedStream.getTracks().forEach(t => t.stop());
    }
  }

  async function submitExam(reason) {
    if (examEnded) return;
    examEnded = true;
    stopAllMonitoring();

    const formData = new FormData(answersForm);
    try {
      const res = await fetch(cfg.submitUrl, { method: 'POST', body: formData });
      const data = await res.json();
      if (reason) alert(reason);
      window.location.href = data.redirect || '/student/dashboard';
    } catch (e) {
      alert('Could not submit automatically. Please check your connection and try again.');
      examEnded = false;
    }
  }

  function endExam(reason) {
    if (examEnded) return;
    if (reason === 'terminated') {
      submitExam('Your attempt has been terminated due to repeated proctoring violations.');
    }
  }

  // ---------- event listeners ----------
  consentStart.addEventListener('click', startExam);

  submitBtn.addEventListener('click', () => {
    if (confirm('Submit your answers now? You will not be able to change them after submitting.')) {
      submitExam(null);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (examActive && !examEnded && document.hidden) {
      reportEvent('tab_hidden', 'violation', 'Student switched tabs or minimized the window');
    }
  });

  document.addEventListener('fullscreenchange', () => {
    if (examActive && !examEnded && !document.fullscreenElement) {
      fsStatus.textContent = 'Exited';
      reportEvent('fullscreen_exit', 'violation', 'Student exited fullscreen mode');
    }
  });

  window.addEventListener('blur', () => {
    if (examActive && !examEnded) {
      reportEvent('window_blur', 'warning', 'Browser window lost focus');
    }
  });

  document.addEventListener('copy', () => {
    if (examActive && !examEnded) reportEvent('copy_paste_attempt', 'warning', 'Copy attempted');
  });
  document.addEventListener('paste', () => {
    if (examActive && !examEnded) reportEvent('copy_paste_attempt', 'warning', 'Paste attempted');
  });
  document.addEventListener('contextmenu', (e) => {
    if (examActive && !examEnded) e.preventDefault();
  });

  window.addEventListener('beforeunload', (e) => {
    if (examActive && !examEnded) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  // ---------- init ----------
  initCamera();
  initFaceModel();
})();
