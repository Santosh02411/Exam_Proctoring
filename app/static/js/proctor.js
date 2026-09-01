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
  const spotCheckOverlay = document.getElementById('spotCheckOverlay');
  const spotCheckPrompt = document.getElementById('spotCheckPrompt');
  const sessionConflictOverlay = document.getElementById('sessionConflictOverlay');

  let sharedStream = null;
  let examActive = false;
  let examEnded = false;
  let identityVerified = false;
  let timerInterval = null;
  let faceCheckInterval = null;
  let snapshotInterval = null;
  let identityCheckInterval = null;
  let audioCheckInterval = null;
  let objectCheckInterval = null;
  let headPoseCheckInterval = null;
  let gazeCheckInterval = null;
  let spotCheckTimer = null;
  let questionTimeTickInterval = null;
  let questionTimeObserver = null;
  let secondsLeft = cfg.durationSeconds;
  let noFaceStreak = 0;
  let audioLoudStreak = 0;
  let phoneStreak = 0;
  let bookStreak = 0;
  let extraPersonStreak = 0;
  let laptopStreak = 0;
  let unauthorizedObjectStreak = 0;
  let lookingAwayStreak = 0;
  let cocoModel = null;
  let cocoModelFailed = false;

  let mediaRecorder = null;
  let recordingChunkIndex = 0;
  let recordingRetryInterval = null;

  let audioCtx = null;
  let analyser = null;
  let audioDataArray = null;

  let questionTimerIntervals = [];

  // ---------- network & exam recovery ----------
  const connDot = document.getElementById('connDot');
  const connStatusText = document.getElementById('connStatusText');
  const connectionBanner = document.getElementById('connectionBanner');

  const LOCAL_BACKUP_KEY = `examBackup_${cfg.attemptId}`;
  const QUEUE_KEY = `examEventQueue_${cfg.attemptId}`;
  const HEARTBEAT_ONLINE_MS = 15000;
  const HEARTBEAT_OFFLINE_MIN_MS = 4000;
  const HEARTBEAT_OFFLINE_MAX_MS = 30000;
  const HEARTBEAT_TIMEOUT_MS = 6000;
  const OFFLINE_STYLE_THRESHOLD_MS = 15000; // how long down before UI escalates from amber to red

  let isOnline = true;
  let offlineSince = null;
  let offlineBackoff = HEARTBEAT_OFFLINE_MIN_MS;
  let heartbeatTimer = null;
  let pendingSubmitReason = null; // set when a final submit failed offline and needs a retry on reconnect
  let localBackupDebounce = null;

  function loadQueue() {
    try {
      const raw = localStorage.getItem(QUEUE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }
  function persistQueue(queue) {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(queue)); } catch (e) { /* storage unavailable — best effort only */ }
  }
  let pendingEvents = loadQueue();

  function queueEvent(eventType, severity, details, confidence) {
    pendingEvents.push({ eventType, severity, details, confidence, ts: Date.now() });
    persistQueue(pendingEvents);
  }

  async function flushPendingEvents() {
    if (!pendingEvents.length) return;
    const toSend = pendingEvents;
    pendingEvents = [];
    persistQueue(pendingEvents);
    for (const item of toSend) {
      await reportEvent(item.eventType, item.severity, item.details, item.confidence);
    }
  }

  // Cheap, frequent local snapshot of whatever's currently in the form —
  // independent of the network, so a browser crash or tab close mid-outage
  // never loses more than a few hundred ms of typing, well inside the gap
  // between server autosaves (every ~20s, or offline entirely).
  function localBackupSave() {
    try {
      const formData = new FormData(answersForm);
      const fields = {};
      for (const [name, value] of formData.entries()) {
        if (fields[name] === undefined) fields[name] = value;
        else if (Array.isArray(fields[name])) fields[name].push(value);
        else fields[name] = [fields[name], value];
      }
      localStorage.setItem(LOCAL_BACKUP_KEY, JSON.stringify({ savedAt: Date.now(), fields }));
    } catch (e) { /* storage full/unavailable — server-side autosave still covers us */ }
  }

  function localBackupRestore() {
    try {
      const raw = localStorage.getItem(LOCAL_BACKUP_KEY);
      if (!raw) return;
      const backup = JSON.parse(raw);
      if (!backup || !backup.fields) return;
      Object.entries(backup.fields).forEach(([name, value]) => {
        const values = Array.isArray(value) ? value : [value];
        const els = answersForm.querySelectorAll(`[name="${CSS.escape(name)}"]`);
        els.forEach((el) => {
          if (el.type === 'checkbox' || el.type === 'radio') {
            el.checked = values.includes(el.value);
          } else if (!el.readOnly) {
            el.value = values[0];
          }
        });
      });
    } catch (e) {
      console.warn('local backup restore failed', e);
    }
  }

  function localBackupClear() {
    try { localStorage.removeItem(LOCAL_BACKUP_KEY); } catch (e) { /* noop */ }
  }

  function setConnectionUI(state) {
    connDot.classList.remove('bad', 'warn');
    if (pendingSubmitReason !== null) {
      connDot.classList.add('warn');
      connStatusText.textContent = 'Finishing submission…';
      connectionBanner.classList.remove('offline');
      connectionBanner.style.display = 'flex';
      connectionBanner.textContent = "Your exam has ended and is finishing submission — this will complete "
        + "automatically the moment you're back online. Please keep this tab open.";
      return;
    }
    if (state === 'online') {
      connStatusText.textContent = 'Online';
      connectionBanner.style.display = 'none';
    } else if (state === 'reconnecting') {
      connDot.classList.add('warn');
      connStatusText.textContent = 'Reconnecting…';
      connectionBanner.classList.remove('offline');
      connectionBanner.style.display = 'flex';
      connectionBanner.textContent = "Connection lost — trying to reconnect. Your answers are saved locally "
        + "and will sync automatically once you're back online.";
    } else if (state === 'offline') {
      connDot.classList.add('bad');
      connStatusText.textContent = 'Offline';
      connectionBanner.classList.add('offline');
      connectionBanner.style.display = 'flex';
      connectionBanner.textContent = "You're offline. Keep this tab open — the exam timer keeps running locally, "
        + "your answers are saved on this device, and everything will sync and resume automatically once your "
        + "connection returns.";
    }
  }

  function handleOffline() {
    const wasOnline = isOnline;
    isOnline = false;
    if (wasOnline) {
      offlineSince = Date.now();
      localBackupSave();
      queueEvent('connection_lost', 'warning', 'Connection to the server was lost');
    }
    offlineBackoff = Math.min(offlineBackoff * 1.6, HEARTBEAT_OFFLINE_MAX_MS);
    const outageMs = offlineSince ? Date.now() - offlineSince : 0;
    setConnectionUI(outageMs > OFFLINE_STYLE_THRESHOLD_MS ? 'offline' : 'reconnecting');
  }

  function handleOnline(data) {
    const wasOffline = !isOnline;
    isOnline = true;
    offlineBackoff = HEARTBEAT_OFFLINE_MIN_MS;
    setConnectionUI('online');

    if (data && typeof data.remaining_seconds === 'number') {
      // Resync to the server's authoritative clock — corrects any drift from
      // being backgrounded/offline instead of trusting a client countdown
      // that kept ticking blind the whole time.
      secondsLeft = data.remaining_seconds;
      timerEl.textContent = fmtTime(Math.max(secondsLeft, 0));
    }

    if (wasOffline) {
      const outageSeconds = offlineSince ? Math.round((Date.now() - offlineSince) / 1000) : null;
      queueEvent(
        'connection_restored',
        outageSeconds !== null && outageSeconds > 120 ? 'violation' : 'warning',
        outageSeconds !== null ? `Connection restored after ~${outageSeconds}s offline` : 'Connection restored'
      );
      offlineSince = null;
      flushPendingEvents();
      flushPendingRecordingChunks();
    }

    if (pendingSubmitReason !== null) {
      const reason = pendingSubmitReason;
      pendingSubmitReason = null;
      retrySubmit(reason);
      return;
    }

    if (data && data.status && data.status !== 'in_progress' && examActive && !examEnded) {
      // The attempt was auto-submitted or terminated server-side (e.g. time
      // fully ran out) while this tab was disconnected — reflect that
      // instead of silently letting a finished exam keep running client-side.
      examEnded = true;
      stopAllMonitoring();
      window.location.href = cfg.resultUrl;
      return;
    }

    if (examActive && !examEnded && wasOffline) {
      saveAnswersNow(); // push whatever accumulated locally while we were offline
    }
  }

  // ---------- Exam Session Device Management ----------
  // Fired when the server tells us (via a heartbeat's session_conflict
  // flag, or a 409 from autosave/submit) that a different tab/device has
  // since taken over this attempt's session — see app.exam_sessions.
  // From this point on the exam is frozen in this tab: further edits
  // can't be saved here, so there's nothing left to protect by continuing
  // to run the camera/timer/checks.
  let sessionSuperseded = false;
  function handleSessionSuperseded() {
    if (sessionSuperseded) return;
    sessionSuperseded = true;
    examEnded = true;
    stopAllMonitoring();
    clearTimeout(heartbeatTimer);
    if (sessionConflictOverlay) sessionConflictOverlay.style.display = 'flex';
  }

  async function pingHeartbeat() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT_MS);
    try {
      const url = cfg.heartbeatUrl + (cfg.sessionToken ? `?session_token=${encodeURIComponent(cfg.sessionToken)}` : '');
      const res = await fetch(url, { method: 'GET', cache: 'no-store', signal: controller.signal });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error(`heartbeat status ${res.status}`);
      const data = await res.json();
      if (data.session_conflict) {
        handleSessionSuperseded();
        return;
      }
      handleOnline(data);
    } catch (e) {
      clearTimeout(timeoutId);
      handleOffline();
    }
  }

  function scheduleNextHeartbeat() {
    clearTimeout(heartbeatTimer);
    const delay = isOnline ? HEARTBEAT_ONLINE_MS : offlineBackoff;
    heartbeatTimer = setTimeout(heartbeatTick, delay);
  }

  async function heartbeatTick() {
    await pingHeartbeat();
    scheduleNextHeartbeat();
  }

  function startConnectionMonitoring() {
    // Local backup + restore work whether or not the exam has formally
    // "started" (consent granted) — the form already exists on the page —
    // so a crash/refresh at any point, including mid-consent, is covered.
    localBackupRestore();
    if (pendingEvents.length && isOnline) flushPendingEvents();

    answersForm.addEventListener('input', () => {
      clearTimeout(localBackupDebounce);
      localBackupDebounce = setTimeout(localBackupSave, 500);
    });

    // navigator.onLine / the browser's online-offline events only reflect
    // the network interface, not real internet reachability, so they're
    // used only as a hint to check sooner — the heartbeat fetch itself is
    // what actually decides online vs offline.
    window.addEventListener('offline', () => pingHeartbeat());
    window.addEventListener('online', () => pingHeartbeat());

    // Exam Session Device Management: best-effort release of this tab's
    // claim on the attempt the moment its page is going away (refresh,
    // navigation, tab close) — see app.exam_sessions.release_session.
    // sendBeacon is specifically designed to survive page teardown (a
    // plain fetch here could get cancelled mid-flight), and only fires
    // while the exam is actually still running in this tab — not after a
    // normal submit already redirected away, and not for a tab that was
    // itself already superseded (nothing to release).
    const releaseSessionBeacon = () => {
      if (!examActive || examEnded || sessionSuperseded || !cfg.sessionToken) return;
      try {
        const payload = JSON.stringify({ attempt_id: cfg.attemptId, session_token: cfg.sessionToken });
        navigator.sendBeacon(cfg.releaseSessionUrl, new Blob([payload], { type: 'application/json' }));
      } catch (e) { /* best-effort — a crash/kill won't fire this anyway */ }
    };
    window.addEventListener('pagehide', releaseSessionBeacon);
    window.addEventListener('beforeunload', releaseSessionBeacon);

    heartbeatTick();
  }

  async function retrySubmit(reason) {
    const formData = new FormData(answersForm);
    formData.append('question_time_spent', JSON.stringify(lastSubmitTimeSnapshot));
    if (cfg.sessionToken) formData.append('session_token', cfg.sessionToken);
    try {
      const res = await fetch(cfg.submitUrl, { method: 'POST', body: formData });
      if (res.status === 409) { handleSessionSuperseded(); return; }
      const data = await res.json();
      localBackupClear();
      persistQueue([]);
      window.location.href = data.redirect || cfg.dashboardUrl;
    } catch (e) {
      pendingSubmitReason = reason; // still down — the next successful heartbeat will retry again
      setConnectionUI(isOnline ? 'reconnecting' : 'offline');
    }
  }

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

  async function reportEvent(eventType, severity, details, confidence) {
    try {
      const res = await fetch(cfg.eventUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          attempt_id: cfg.attemptId,
          event_type: eventType,
          severity: severity,
          details: details || '',
          confidence: (typeof confidence === 'number' && !isNaN(confidence)) ? confidence : undefined
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
      console.warn('reportEvent failed, will retry once back online', e);
      queueEvent(eventType, severity, details, confidence);
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
      await faceapi.nets.faceLandmark68TinyNet.loadFromUri(cfg.modelsUrl);
      await faceapi.nets.faceRecognitionNet.loadFromUri(cfg.modelsUrl);
      modelStatus.textContent = 'Ready';
      maybeEnableVerify();
    } catch (e) {
      modelStatus.textContent = 'Failed to load — proctoring model unavailable.';
      console.error(e);
    }
  }

  async function initObjectModel() {
    // Object detection (phone/book/paper/laptop/extra-person/other
    // unauthorized objects) is an additive layer on top of face-based
    // proctoring, not a hard requirement — if the CDN is slow/blocked, the
    // exam still proceeds on face/tab/fullscreen checks alone rather than
    // blocking the student from starting.
    try {
      if (typeof cocoSsd === 'undefined') { cocoModelFailed = true; return; }
      cocoModel = await cocoSsd.load({ base: 'lite_mobilenet_v2' });
    } catch (e) {
      cocoModelFailed = true;
      console.warn('object detection model unavailable', e);
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

  // Not a model's own confidence score (euclidean distance isn't a
  // probability) — a simple, honest normalization of how far the distance
  // exceeds the match threshold, clamped to [0,1], so a distance right at
  // the threshold reads as low-confidence and a wildly different face
  // reads as high-confidence rather than showing a raw distance number.
  function mismatchConfidence(distance) {
    return Math.max(0, Math.min((distance - cfg.faceMatchThreshold) / cfg.faceMatchThreshold, 1));
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
      reportEvent('identity_mismatch', 'warning', `Consent-stage mismatch, distance=${result.distance.toFixed(3)}`, mismatchConfidence(result.distance));
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
    scheduleNextSpotCheck();
    startAudioMonitoring();
    initQuestionTimeTracking();
    startRecording();
    startPerQuestionTimers();
    startSectionTimers();
    startAutosave();
    startObjectDetection();
    startHeadPoseMonitoring();
    startGazeMonitoring();
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

  // ---------- per-question time tracking (Advanced Analytics) ----------
  // How long each question was on screen — not "actively worked on"; a
  // question scrolled into view and left there while the student thinks
  // still accrues time. Split evenly across whatever's simultaneously
  // >=50% visible, and paused whenever the tab is hidden/unfocused so
  // background time never counts. This is a secondary analytics signal,
  // not exam-critical, so failures here are silently tolerated.
  const questionTimeAccum = {}; // qid -> accumulated ms since last successful send
  const activeQuestionIds = new Set();
  let lastQuestionTimeTick = Date.now();
  let lastSubmitTimeSnapshot = {};

  function tickQuestionTime() {
    const now = Date.now();
    const delta = now - lastQuestionTimeTick;
    lastQuestionTimeTick = now;
    if (document.visibilityState !== 'visible' || !document.hasFocus() || !activeQuestionIds.size) return;
    const share = delta / activeQuestionIds.size;
    activeQuestionIds.forEach((qid) => {
      questionTimeAccum[qid] = (questionTimeAccum[qid] || 0) + share;
    });
  }

  function initQuestionTimeTracking() {
    const blocks = document.querySelectorAll('.qcard[data-qid]');
    if (!blocks.length || typeof IntersectionObserver === 'undefined') return;
    questionTimeObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        const qid = entry.target.getAttribute('data-qid');
        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
          activeQuestionIds.add(qid);
        } else {
          activeQuestionIds.delete(qid);
        }
      });
    }, { threshold: [0, 0.5, 1] });
    blocks.forEach((b) => questionTimeObserver.observe(b));
    lastQuestionTimeTick = Date.now();
    questionTimeTickInterval = setInterval(tickQuestionTime, 1000);
  }

  function questionTimeSnapshotSeconds() {
    tickQuestionTime(); // fold in whatever's accumulated since the last tick before snapshotting
    const out = {};
    Object.entries(questionTimeAccum).forEach(([qid, ms]) => {
      const secs = Math.round(ms / 1000);
      if (secs > 0) out[qid] = secs;
    });
    return out;
  }

  // Only clears what was actually confirmed sent — if the request never
  // reaches the server (or the response is lost), the delta stays queued
  // for the next attempt rather than silently disappearing.
  function clearQuestionTimeAccum(sentSeconds) {
    Object.entries(sentSeconds).forEach(([qid, secs]) => {
      if (questionTimeAccum[qid] !== undefined) {
        questionTimeAccum[qid] = Math.max(0, questionTimeAccum[qid] - secs * 1000);
      }
    });
  }

  async function saveAnswersNow() {
    if (!examActive || examEnded) return;
    localBackupSave(); // cheap and network-independent — take this snapshot regardless of connectivity
    if (!isOnline) return; // no point attempting a request we already know will fail
    const timeSnapshot = questionTimeSnapshotSeconds();
    try {
      const formData = new FormData(answersForm);
      formData.append('question_time_spent', JSON.stringify(timeSnapshot));
      if (cfg.sessionToken) formData.append('session_token', cfg.sessionToken);
      const res = await fetch(cfg.autosaveUrl, { method: 'POST', body: formData });
      if (res.status === 409) { handleSessionSuperseded(); return; }
      clearQuestionTimeAccum(timeSnapshot);
      localBackupClear(); // synced to the server — the local fallback copy is redundant until the next edit
    } catch (e) {
      console.warn('autosave failed', e);
      handleOffline(); // a failed autosave is itself a connectivity signal — react immediately rather than waiting for the next heartbeat
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
          const minScore = Math.min(...detections.map((d) => d.score));
          reportEvent('multiple_faces', 'violation', `${detections.length} faces detected in frame`, minScore);
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

  // ---------- object detection: phone / book / paper / laptop / extra person / other ----------
  // Uses COCO-SSD (a standard general-purpose object detector, not trained
  // specifically for exam proctoring) — it recognizes 80 common object
  // classes, including 'cell phone', 'book', 'laptop', and 'person'. Two
  // honest limitations worth knowing about:
  //   - It does NOT have a class for earphones/earbuds/headphones — no
  //     standard general-purpose detector reliably distinguishes those
  //     (they're small, low-contrast, and shaped differently depending on
  //     model), so they can't be flagged this way. A future release could
  //     revisit this with a small custom-trained classifier if it becomes
  //     a priority.
  //   - "book" covers loose paper/notes too, since COCO has no separate
  //     class for a sheet of paper — reported as book_detected either way.
  // Everything else COCO can name that would be unusual to see in frame
  // during an exam (a remote, a tablet, a TV/monitor, etc.) is bucketed
  // into a single 'unauthorized_object_detected' catch-all rather than a
  // dedicated event per class, since most of these are rare enough that a
  // dedicated type per class would be overkill.
  //
  // Each detection type requires 2 consecutive positive checks (~10s
  // apart) before being logged as a violation, the same debounce margin
  // used for no_face, to cut down on single-frame false positives (glare,
  // a hand near the face, etc.)
  const OBJECT_CHECK_INTERVAL_MS = 5000;
  const OBJECT_CONFIDENCE_THRESHOLD = 0.6;
  const OBJECT_STREAK_REQUIRED = 2;
  // Any laptop in the camera frame is suspicious by construction — the
  // student's own exam device is a screen/keyboard they're typing into,
  // not something the webcam should be seeing from the outside, so unlike
  // 'person' there's no "expected count" to subtract before flagging one.
  const OTHER_UNAUTHORIZED_CLASSES = new Set([
    'remote', 'tablet', 'tv', 'keyboard', 'mouse', 'clock', 'scissors',
  ]);

  function startObjectDetection() {
    objectCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      if (!cocoModel) return; // still loading, or failed to load — skip silently
      try {
        const predictions = await cocoModel.detect(camPreview);
        const relevant = predictions.filter((p) => p.score >= OBJECT_CONFIDENCE_THRESHOLD);

        const phoneSeen = relevant.some((p) => p.class === 'cell phone');
        const bookSeen = relevant.some((p) => p.class === 'book');
        const laptopSeen = relevant.some((p) => p.class === 'laptop');
        const otherSeen = relevant.filter((p) => OTHER_UNAUTHORIZED_CLASSES.has(p.class));
        const personPredictions = relevant.filter((p) => p.class === 'person');
        const personCount = personPredictions.length;

        phoneStreak = phoneSeen ? phoneStreak + 1 : 0;
        bookStreak = bookSeen ? bookStreak + 1 : 0;
        laptopStreak = laptopSeen ? laptopStreak + 1 : 0;
        unauthorizedObjectStreak = otherSeen.length ? unauthorizedObjectStreak + 1 : 0;
        extraPersonStreak = personCount >= 2 ? extraPersonStreak + 1 : 0;

        if (phoneStreak >= OBJECT_STREAK_REQUIRED) {
          const score = Math.max(...relevant.filter((p) => p.class === 'cell phone').map((p) => p.score));
          reportEvent('phone_detected', 'violation', 'A cell phone was detected in the camera frame', score);
          phoneStreak = 0;
        }
        if (bookStreak >= OBJECT_STREAK_REQUIRED) {
          const score = Math.max(...relevant.filter((p) => p.class === 'book').map((p) => p.score));
          reportEvent('book_detected', 'violation', 'A book, notes, or paper were detected in the camera frame', score);
          bookStreak = 0;
        }
        if (laptopStreak >= OBJECT_STREAK_REQUIRED) {
          const score = Math.max(...relevant.filter((p) => p.class === 'laptop').map((p) => p.score));
          reportEvent('laptop_detected', 'violation', 'An additional laptop or screen was detected in the camera frame', score);
          laptopStreak = 0;
        }
        if (unauthorizedObjectStreak >= OBJECT_STREAK_REQUIRED) {
          const classNames = [...new Set(otherSeen.map((p) => p.class))].join(', ');
          const score = Math.max(...otherSeen.map((p) => p.score));
          reportEvent('unauthorized_object_detected', 'violation', `Unauthorized object(s) detected in the camera frame: ${classNames}`, score);
          unauthorizedObjectStreak = 0;
        }
        if (extraPersonStreak >= OBJECT_STREAK_REQUIRED) {
          const score = Math.min(...personPredictions.map((p) => p.score));
          reportEvent('extra_person_detected', 'violation', `${personCount} people detected in the camera frame`, score);
          extraPersonStreak = 0;
        }
      } catch (e) {
        console.warn('object detection error', e);
      }
    }, OBJECT_CHECK_INTERVAL_MS);
  }

  // ---------- head-pose ("looking away") ----------
  // Approximate yaw/pitch from the 68-point face landmarks already loaded
  // for identity verification — NOT true gaze/eye-tracking (see the
  // separate gaze-tracking section below for that). This only tells us
  // which way the *head* is pointed, which is a coarse but honest proxy
  // for "not looking at the screen." A generous threshold and a
  // sustained-streak requirement (~15s) keep the single-direction
  // "looking_away" event from firing on brief, normal glances away.
  const YAW_RATIO_THRESHOLD = 0.18;
  const PITCH_RATIO_THRESHOLD = 0.16;
  const HEAD_POSE_STREAK_REQUIRED = 3;

  function estimateYawRatio(landmarks) {
    const jaw = landmarks.getJawOutline();
    const nose = landmarks.getNose();
    const leftEye = landmarks.getLeftEye();
    const rightEye = landmarks.getRightEye();
    const faceWidth = Math.abs(jaw[16].x - jaw[0].x);
    if (!faceWidth) return 0;
    const eyeMidX = (leftEye[0].x + rightEye[3].x) / 2;
    const noseTipX = nose[3].x;
    return (noseTipX - eyeMidX) / faceWidth;
  }

  // Vertical counterpart to estimateYawRatio: how far the nose tip sits
  // above/below the eye line, relative to face height — a positive value
  // means the head is tipped down (nose below the eye line further than
  // usual), negative means tipped up. Same "coarse proxy, not precise 3D
  // pose" caveat applies.
  function estimatePitchRatio(landmarks) {
    const jaw = landmarks.getJawOutline();
    const nose = landmarks.getNose();
    const leftEye = landmarks.getLeftEye();
    const rightEye = landmarks.getRightEye();
    const faceHeight = Math.abs(jaw[8].y - ((leftEye[1].y + rightEye[1].y) / 2));
    if (!faceHeight) return 0;
    const eyeMidY = (leftEye[1].y + rightEye[1].y) / 2;
    const noseTipY = nose[3].y;
    return (noseTipY - eyeMidY) / faceHeight;
  }

  // ---------- advanced head-pose: repeated-movement pattern ----------
  // Beyond the single "sustained turn in one direction" check above, this
  // classifies each sample into a coarse direction bucket (left/right/up/
  // down/center) and watches for a *pattern* of repeated excursions away
  // from center within a short window — e.g. several separate left/right
  // glances in under a minute — which reads differently than one sustained
  // turn (that could just be someone stretching their neck) and differently
  // than isolated occasional glances (normal). Reported as its own event
  // type, "repeated_head_movement", alongside (not instead of)
  // "looking_away".
  const HEAD_DIRECTION_HISTORY_WINDOW_MS = 60000;
  const HEAD_DIRECTION_EXCURSIONS_REQUIRED = 4;
  const HEAD_DIRECTION_REPORT_COOLDOWN_MS = 45000;
  let headDirectionHistory = []; // [{ direction, at }]
  let wasOffCenter = false;
  let lastRepeatedMovementReportAt = 0;

  function classifyHeadDirection(yawRatio, pitchRatio) {
    if (Math.abs(yawRatio) >= YAW_RATIO_THRESHOLD && Math.abs(yawRatio) >= Math.abs(pitchRatio)) {
      return yawRatio > 0 ? 'right' : 'left';
    }
    if (Math.abs(pitchRatio) >= PITCH_RATIO_THRESHOLD) {
      return pitchRatio > 0 ? 'down' : 'up';
    }
    return 'center';
  }

  function recordHeadDirectionSample(direction) {
    const now = Date.now();
    const offCenter = direction !== 'center';
    // Count a transition INTO an off-center direction as one "excursion" —
    // so a single sustained turn is one excursion, not dozens of samples.
    if (offCenter && !wasOffCenter) {
      headDirectionHistory.push({ direction, at: now });
    }
    wasOffCenter = offCenter;

    headDirectionHistory = headDirectionHistory.filter((h) => now - h.at <= HEAD_DIRECTION_HISTORY_WINDOW_MS);

    if (
      headDirectionHistory.length >= HEAD_DIRECTION_EXCURSIONS_REQUIRED &&
      now - lastRepeatedMovementReportAt >= HEAD_DIRECTION_REPORT_COOLDOWN_MS
    ) {
      const directions = [...new Set(headDirectionHistory.map((h) => h.direction))];
      reportEvent(
        'repeated_head_movement',
        'violation',
        `${headDirectionHistory.length} separate head-turn excursions (${directions.join('/')}) within the last minute`
      );
      lastRepeatedMovementReportAt = now;
      headDirectionHistory = [];
    }
  }

  function startHeadPoseMonitoring() {
    headPoseCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      if (!faceapi.nets.faceLandmark68TinyNet.isLoaded) return;
      try {
        const result = await faceapi
          .detectSingleFace(camPreview, new faceapi.TinyFaceDetectorOptions())
          .withFaceLandmarks(true);
        if (!result) { lookingAwayStreak = 0; return; } // no_face is already handled separately
        const yawRatio = estimateYawRatio(result.landmarks);
        const pitchRatio = estimatePitchRatio(result.landmarks);

        recordHeadDirectionSample(classifyHeadDirection(yawRatio, pitchRatio));

        const offAxis = Math.max(Math.abs(yawRatio) - YAW_RATIO_THRESHOLD, Math.abs(pitchRatio) - PITCH_RATIO_THRESHOLD);
        if (Math.abs(yawRatio) >= YAW_RATIO_THRESHOLD || Math.abs(pitchRatio) >= PITCH_RATIO_THRESHOLD) {
          lookingAwayStreak += 1;
          if (lookingAwayStreak >= HEAD_POSE_STREAK_REQUIRED) {
            reportEvent(
              'looking_away', 'violation',
              `Sustained head turn away from screen (yaw ${yawRatio.toFixed(2)}, pitch ${pitchRatio.toFixed(2)})`
            );
            lookingAwayStreak = 0;
          }
        } else {
          lookingAwayStreak = 0;
        }
      } catch (e) {
        console.warn('head-pose check error', e);
      }
    }, 5000);
  }

  // ---------- AI gaze tracking (eye/iris position) ----------
  // Distinct from head-pose above: this estimates where the EYES are
  // pointed, not just which way the head is turned — someone can hold
  // their head still and just move their eyes to read off-screen material.
  // Standard webcams and face-api.js's 68-point landmarks don't give true
  // iris landmarks, so this uses a classic lightweight technique ("dark
  // pupil" tracking): crop the eye region using the existing eye-contour
  // landmarks, then find the darkest cluster of pixels within it — the
  // iris/pupil is reliably darker than the surrounding sclera and skin —
  // and use that cluster's offset from the eye's center as a gaze-
  // direction estimate. It's a heuristic, not biometric-grade eye
  // tracking, but it's a genuine, honest eye-position signal rather than
  // a head-pose proxy relabeled as "gaze."
  const GAZE_CHECK_INTERVAL_MS = 1000;
  const GAZE_OFFSET_THRESHOLD = 0.22; // fraction of eye width/height counted as "off-center"
  const GAZE_AWAY_STREAK_REQUIRED = 4; // ~4 consecutive seconds off-center before flagging
  let gazeAwayStreak = 0;
  let gazeCanvas = null;
  let gazeCtx = null;

  function eyeBoundingBox(eyePoints, padding) {
    const xs = eyePoints.map((p) => p.x);
    const ys = eyePoints.map((p) => p.y);
    const minX = Math.min(...xs) - padding;
    const maxX = Math.max(...xs) + padding;
    const minY = Math.min(...ys) - padding;
    const maxY = Math.max(...ys) + padding;
    return { x: minX, y: minY, width: Math.max(maxX - minX, 1), height: Math.max(maxY - minY, 1) };
  }

  // Returns { dx, dy } — the darkest-pixel-cluster centroid's offset from
  // the eye box center, each normalized to roughly [-0.5, 0.5] of the eye's
  // width/height. Returns null if the crop is degenerate or the video isn't
  // ready to sample from yet.
  function estimateGazeOffsetForEye(videoEl, eyePoints) {
    const box = eyeBoundingBox(eyePoints, 2);
    if (box.width < 4 || box.height < 4) return null;
    if (!gazeCanvas) {
      gazeCanvas = document.createElement('canvas');
      gazeCtx = gazeCanvas.getContext('2d', { willReadFrequently: true });
    }
    gazeCanvas.width = box.width;
    gazeCanvas.height = box.height;
    try {
      gazeCtx.drawImage(videoEl, box.x, box.y, box.width, box.height, 0, 0, box.width, box.height);
      const { data } = gazeCtx.getImageData(0, 0, box.width, box.height);

      // Darkness threshold relative to this crop's own brightness range,
      // rather than a fixed constant, so it holds up across different
      // webcams/lighting rather than only working in bright, even light.
      let minLum = 255, maxLum = 0;
      const lums = new Float32Array(box.width * box.height);
      for (let i = 0, p = 0; i < data.length; i += 4, p += 1) {
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        lums[p] = lum;
        if (lum < minLum) minLum = lum;
        if (lum > maxLum) maxLum = lum;
      }
      const range = maxLum - minLum;
      if (range < 8) return null; // too flat/washed-out a crop to say anything meaningful
      const darkThreshold = minLum + range * 0.35;

      let sumX = 0, sumY = 0, count = 0;
      for (let py = 0; py < box.height; py += 1) {
        for (let px = 0; px < box.width; px += 1) {
          if (lums[py * box.width + px] <= darkThreshold) {
            sumX += px;
            sumY += py;
            count += 1;
          }
        }
      }
      if (count < 3) return null;
      const centroidX = sumX / count;
      const centroidY = sumY / count;
      return {
        dx: (centroidX - box.width / 2) / box.width,
        dy: (centroidY - box.height / 2) / box.height,
      };
    } catch (e) {
      return null; // e.g. a transient decode/security error reading the frame — just skip this sample
    }
  }

  function startGazeMonitoring() {
    gazeCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      if (!faceapi.nets.faceLandmark68TinyNet.isLoaded) return;
      try {
        const result = await faceapi
          .detectSingleFace(camPreview, new faceapi.TinyFaceDetectorOptions())
          .withFaceLandmarks(true);
        if (!result) { gazeAwayStreak = 0; return; }

        const leftOffset = estimateGazeOffsetForEye(camPreview, result.landmarks.getLeftEye());
        const rightOffset = estimateGazeOffsetForEye(camPreview, result.landmarks.getRightEye());
        const samples = [leftOffset, rightOffset].filter(Boolean);
        if (!samples.length) { gazeAwayStreak = 0; return; }

        const avgDx = samples.reduce((s, o) => s + o.dx, 0) / samples.length;
        const avgDy = samples.reduce((s, o) => s + o.dy, 0) / samples.length;

        if (Math.abs(avgDx) >= GAZE_OFFSET_THRESHOLD || Math.abs(avgDy) >= GAZE_OFFSET_THRESHOLD) {
          gazeAwayStreak += 1;
          if (gazeAwayStreak >= GAZE_AWAY_STREAK_REQUIRED) {
            const seconds = Math.round((gazeAwayStreak * GAZE_CHECK_INTERVAL_MS) / 1000);
            reportEvent(
              'gaze_away', 'violation',
              `Eyes drifted off-screen for ~${seconds}s (offset dx=${avgDx.toFixed(2)}, dy=${avgDy.toFixed(2)})`
            );
            gazeAwayStreak = 0;
          }
        } else {
          gazeAwayStreak = 0;
        }
      } catch (e) {
        console.warn('gaze check error', e);
      }
    }, GAZE_CHECK_INTERVAL_MS);
  }

  function startIdentityRecheck() {
    if (!cfg.referenceDescriptor) return;
    identityCheckInterval = setInterval(async () => {
      if (!examActive || examEnded) return;
      try {
        const result = await checkIdentity(camPreview);
        if (!result.ok && !result.skipped && result.reason !== 'no_face') {
          reportEvent('identity_mismatch', 'violation', `In-exam mismatch, distance=${result.distance.toFixed(3)}`, mismatchConfidence(result.distance));
        }
      } catch (e) {
        console.warn('identity re-check error', e);
      }
    }, 25000);
  }

  // ---------- random identity spot checks ----------
  // Distinct from the silent 25s background check above: this is a visible,
  // active re-verification at an unpredictable interval (so its timing can't
  // be anticipated/gamed), pairing a face-match against the enrolled
  // reference with the same blink-based liveness check used at enrollment —
  // catching, for example, someone swapping in a static photo mid-exam.
  const SPOTCHECK_EAR_THRESHOLD = 0.22;

  function eyeAspectRatio(eye) {
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const vertical1 = dist(eye[1], eye[5]);
    const vertical2 = dist(eye[2], eye[4]);
    const horizontal = dist(eye[0], eye[3]);
    return horizontal ? (vertical1 + vertical2) / (2 * horizontal) : 0;
  }

  async function runLivenessSample(videoEl, durationMs) {
    let blinkCount = 0;
    let eyesWereClosed = false;
    let lastOpenDescriptor = null;
    const deadline = Date.now() + durationMs;

    while (Date.now() < deadline) {
      if (!examActive || examEnded) break;
      const result = await faceapi
        .detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions())
        .withFaceLandmarks()
        .withFaceDescriptor();

      if (result) {
        const ear = (eyeAspectRatio(result.landmarks.getLeftEye()) + eyeAspectRatio(result.landmarks.getRightEye())) / 2;
        if (ear < SPOTCHECK_EAR_THRESHOLD) {
          eyesWereClosed = true;
        } else {
          if (eyesWereClosed) blinkCount += 1;
          eyesWereClosed = false;
          lastOpenDescriptor = result.descriptor;
        }
        if (spotCheckPrompt) spotCheckPrompt.textContent = `Blink naturally… (${blinkCount} blink${blinkCount === 1 ? '' : 's'} detected)`;
      } else if (spotCheckPrompt) {
        spotCheckPrompt.textContent = 'No face detected — please look at the camera.';
      }
      await new Promise((r) => setTimeout(r, 200));
    }

    return { blinkCount, live: blinkCount >= 1, descriptor: lastOpenDescriptor };
  }

  function scheduleNextSpotCheck() {
    if (!cfg.referenceDescriptor) return; // nothing enrolled to check against
    const minMs = (cfg.identitySpotcheckMinSeconds || 180) * 1000;
    const maxMs = Math.max(cfg.identitySpotcheckMaxSeconds || 420, cfg.identitySpotcheckMinSeconds || 180) * 1000;
    const delay = minMs + Math.random() * Math.max(maxMs - minMs, 0);
    clearTimeout(spotCheckTimer);
    spotCheckTimer = setTimeout(runSpotCheck, delay);
  }

  async function runSpotCheck() {
    if (!examActive || examEnded || !cfg.referenceDescriptor) return;
    spotCheckOverlay.style.display = 'flex';
    if (spotCheckPrompt) spotCheckPrompt.textContent = 'Starting…';

    try {
      const liveness = await runLivenessSample(camPreview, 6000);
      if (!examActive || examEnded) return; // exam ended mid-check — nothing to report

      if (!liveness.descriptor) {
        reportEvent('identity_spotcheck_failed', 'violation', 'No face detected during random spot check');
      } else {
        const distance = faceapi.euclideanDistance(liveness.descriptor, cfg.referenceDescriptor);
        const matched = distance <= cfg.faceMatchThreshold;
        if (matched && liveness.live) {
          reportEvent('identity_spotcheck_passed', 'warning', `Spot check passed, distance=${distance.toFixed(3)}, blinks=${liveness.blinkCount}`);
        } else if (!liveness.live) {
          reportEvent('liveness_check_failed', 'violation', `Spot check liveness not confirmed, distance=${distance.toFixed(3)}`);
        } else {
          reportEvent('identity_spotcheck_failed', 'violation', `Spot check mismatch, distance=${distance.toFixed(3)}`, mismatchConfidence(distance));
        }
      }
    } catch (e) {
      console.warn('spot check error', e);
    } finally {
      spotCheckOverlay.style.display = 'none';
      if (examActive && !examEnded) scheduleNextSpotCheck();
    }
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

  // ---------- Advanced Network Recovery: recording chunk resilience ----------
  // Proctoring video is captured in ~30s chunks and uploaded as each one
  // completes (see startRecording below); a chunk that fails to upload
  // during an outage would otherwise just be lost — silently leaving a
  // gap in the footage for exactly the period an admin reviewing a
  // flagged attempt would most want to see. Failed chunks are kept here
  // and retried once connectivity is back (see flushPendingRecordingChunks,
  // called from handleOnline) and periodically in between, in case a
  // single chunk failed for a transient reason without a full outage
  // being detected.
  //
  // This queue is memory-only, not persisted to localStorage like
  // pendingEvents — video blobs are far too large for that, and would
  // fail or blow past typical per-origin storage quotas. That means it
  // can survive a brief network drop but not a full page reload/crash —
  // an intentional scope limit for "temporary" loss where the tab stays
  // open, not a guarantee against every possible failure. It's also
  // capped (MAX_QUEUED_RECORDING_CHUNKS) so a very long outage drops the
  // oldest queued chunks instead of growing memory without bound; a
  // dropped chunk just leaves a short gap in the recording rather than
  // crashing the tab.
  let pendingRecordingChunks = [];
  const MAX_QUEUED_RECORDING_CHUNKS = 20; // ~10 minutes of footage at the default 30s chunk size
  const RECORDING_RETRY_INTERVAL_MS = 20000;

  function queueRecordingChunk(blob, index) {
    pendingRecordingChunks.push({ blob, index });
    if (pendingRecordingChunks.length > MAX_QUEUED_RECORDING_CHUNKS) {
      const dropped = pendingRecordingChunks.shift();
      console.warn(`recording chunk ${dropped.index} dropped — retry queue full`);
    }
  }

  function flushPendingRecordingChunks() {
    if (!pendingRecordingChunks.length || !isOnline) return;
    const toSend = pendingRecordingChunks;
    pendingRecordingChunks = [];
    toSend.forEach((item) => uploadRecordingChunk(item.blob, item.index));
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
    recordingRetryInterval = setInterval(flushPendingRecordingChunks, RECORDING_RETRY_INTERVAL_MS);
  }

  function uploadRecordingChunk(blob, index) {
    const fd = new FormData();
    fd.append('attempt_id', cfg.attemptId);
    fd.append('chunk_index', index);
    fd.append('chunk', blob, `chunk_${index}.webm`);
    fetch(cfg.recordingChunkUrl, { method: 'POST', body: fd })
      .then((res) => {
        if (!res.ok) throw new Error(`chunk upload status ${res.status}`);
      })
      .catch((e) => {
        console.warn('recording chunk upload failed, queued for retry', e);
        queueRecordingChunk(blob, index);
      });
  }

  function stopAllMonitoring() {
    clearInterval(timerInterval);
    clearInterval(faceCheckInterval);
    clearInterval(snapshotInterval);
    clearInterval(identityCheckInterval);
    clearInterval(audioCheckInterval);
    clearInterval(objectCheckInterval);
    clearInterval(headPoseCheckInterval);
    clearInterval(gazeCheckInterval);
    clearInterval(recordingRetryInterval);
    clearTimeout(spotCheckTimer);
    spotCheckOverlay.style.display = 'none';
    clearInterval(questionTimeTickInterval);
    if (questionTimeObserver) questionTimeObserver.disconnect();
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
    localBackupSave(); // last-resort snapshot in case this very submit is what fails below

    const formData = new FormData(answersForm);
    lastSubmitTimeSnapshot = questionTimeSnapshotSeconds();
    formData.append('question_time_spent', JSON.stringify(lastSubmitTimeSnapshot));
    try {
      const res = await fetch(cfg.submitUrl, { method: 'POST', body: formData });
      const data = await res.json();
      localBackupClear();
      persistQueue([]);
      if (reason) alert(reason);
      window.location.href = data.redirect || cfg.dashboardUrl;
    } catch (e) {
      // Offline right at submission time (e.g. time ran out mid-outage).
      // The attempt isn't lost — answers are backed up locally and the
      // last server-side autosave — this retries automatically the moment
      // the connection returns, via handleOnline()/retrySubmit().
      pendingSubmitReason = reason;
      setConnectionUI(isOnline ? 'reconnecting' : 'offline');
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
  initObjectModel();
  startConnectionMonitoring();
})();
