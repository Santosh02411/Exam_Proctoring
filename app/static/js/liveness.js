/* Shared blink-based liveness check (eye-aspect-ratio over face-api.js
 * landmarks), used both at ID enrollment (student/enroll_face.html) and for
 * random in-exam identity spot checks (proctor.js's runSpotCheck). Pulled
 * out into one file on purpose: this logic used to be copy-pasted into both
 * places separately, and both copies shared the same real bug — a fixed,
 * absolute EAR threshold (0.22) that assumes every person's eyes, camera
 * angle, and lighting produce the same "closed" number. A single shared
 * copy means there's only one place left to get this right.
 *
 * This went through two real-world rounds:
 *   1. First fix: made the threshold relative to a rolling AVERAGE of this
 *      person's own recent open-eye EAR, instead of a hardcoded constant.
 *      Still failed for a real user: a rolling average can drift downward
 *      over several seconds of normal eyelid relaxation (very common when
 *      someone is deliberately staring at a camera), and once it drifts, a
 *      genuine blink's dip is being measured against an already-lowered
 *      baseline instead of the person's actual "fully open" reading — so
 *      it can fail to register as a drop at all.
 *   2. This version: the baseline is now a CEILING (the highest sustained
 *      open-eye reading seen so far, smoothed over a few samples so a
 *      single noisy frame can't set it) that only ever moves up, never
 *      down — a blink can't drag down the very reference it's measured
 *      against. There's also a second, independent signal: if the
 *      eye-openness reading simply varies meaningfully over the whole
 *      check (`EAR_RANGE_FALLBACK`), that alone is accepted as liveness
 *      even without a cleanly-resolved open→closed→open cycle — a frozen
 *      printed photo produces almost no frame-to-frame variation at all
 *      (the model is re-run on the same unchanging pixels), while any real
 *      person's face never holds perfectly still for several seconds, even
 *      without a deliberate blink. This is deliberately the more forgiving
 *      of the two signals — the point of this check (per the enrollment
 *      page's own description) is to rule out a static photo, not to
 *      perform forensic-grade analysis, so it should err toward not
 *      rejecting a real, cooperative person.
 *
 * The core decision logic (`createTracker`) takes plain EAR numbers in and
 * is independent of face-api.js, the DOM, or a camera — see
 * tests/js/test_liveness.mjs, which drives it with synthetic EAR sequences
 * (a clean blink, a slow drift with no blink, a frozen/static sequence, a
 * squint-without-a-full-blink) to pin down its behavior directly, rather
 * than only ever being exercised by an actual webcam.
 */
(function (global) {
  const EAR_BLINK_RATIO = 0.80; // a blink = EAR drops to <=80% of the open-eye ceiling established so far
  const EAR_FALLBACK_THRESHOLD = 0.24; // only used before any ceiling exists yet (the first couple of samples)
  const CEILING_SMOOTHING_SAMPLES = 3; // a new ceiling candidate is the average of this many recent open samples, not one single (possibly noisy) frame
  const EAR_RANGE_FALLBACK = 0.10; // (max-min)/max over the WHOLE session >= this counts as liveness even with no clean blink
  const MIN_SAMPLES_FOR_RANGE_FALLBACK = 12; // don't trust the range signal off just a few samples
  const DESCRIPTOR_REFRESH_CHANCE = 0.3; // re-grab the (expensive) face descriptor on this fraction of good open-eye frames
  const POLL_GAP_MS = 80;

  function eyeAspectRatio(eye) {
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const vertical1 = dist(eye[1], eye[5]);
    const vertical2 = dist(eye[2], eye[4]);
    const horizontal = dist(eye[0], eye[3]);
    return horizontal ? (vertical1 + vertical2) / (2 * horizontal) : 0;
  }

  /**
   * Pure decision state machine — no DOM/face-api dependency, so it can be
   * driven directly with plain numbers in tests. Call update(ear) once per
   * sample, then finalize() for the overall result.
   */
  function createTracker() {
    let ceiling = null;
    let blinkCount = 0;
    let eyesWereClosed = false;
    let recentOpen = []; // last few OPEN readings, for smoothing the ceiling
    const allSamples = [];

    function update(ear) {
      allSamples.push(ear);
      const threshold = ceiling !== null ? ceiling * EAR_BLINK_RATIO : EAR_FALLBACK_THRESHOLD;
      const isOpen = ear >= threshold;

      if (!isOpen) {
        eyesWereClosed = true;
      } else {
        if (eyesWereClosed) blinkCount += 1;
        eyesWereClosed = false;

        recentOpen.push(ear);
        if (recentOpen.length > CEILING_SMOOTHING_SAMPLES) recentOpen.shift();
        // Only set/raise the ceiling once a FULL smoothing window of open
        // samples has accumulated -- classification before that uses
        // EAR_FALLBACK_THRESHOLD instead (see the `threshold` line above).
        // A bare `|| ceiling === null` escape hatch here would let a
        // single noisy first-open-sample become the ceiling outright with
        // no smoothing at all, which is exactly the outlier vulnerability
        // this smoothing exists to prevent.
        if (recentOpen.length >= CEILING_SMOOTHING_SAMPLES) {
          const candidate = recentOpen.reduce((a, b) => a + b, 0) / recentOpen.length;
          if (ceiling === null || candidate > ceiling) ceiling = candidate;
        }
      }

      return { isOpen, blinkCount, ceiling, threshold };
    }

    function finalize() {
      if (allSamples.length === 0) {
        return { live: false, blinkCount: 0, viaMovementFallback: false, max: null, min: null, range: 0, sampleCount: 0 };
      }
      const max = Math.max(...allSamples);
      const min = Math.min(...allSamples);
      const range = max > 0 ? (max - min) / max : 0;
      const movementFallback = blinkCount === 0
        && allSamples.length >= MIN_SAMPLES_FOR_RANGE_FALLBACK
        && range >= EAR_RANGE_FALLBACK;
      return {
        live: blinkCount >= 1 || movementFallback, blinkCount, viaMovementFallback: movementFallback,
        max, min, range, sampleCount: allSamples.length,
      };
    }

    return { update, finalize };
  }

  /**
   * Polls `videoEl` for up to `durationMs`, looking for liveness evidence
   * (a natural blink, or — failing that — enough natural eye-openness
   * variation that a frozen photo couldn't produce). Returns
   * { live, blinkCount, descriptor, ...finalize() fields } — `descriptor`
   * is the 128-d face descriptor from the best available open-eye frame,
   * or null if no face was ever confidently detected open.
   *
   * `options`:
   *   onTick(text)     — called with a short human-readable status string
   *                       after each sample (face-api detector included).
   *   shouldAbort()     — polled each iteration; returning true stops early
   *                       (e.g. the exam ended mid-check) with whatever
   *                       was captured so far.
   *   tinyOptions       — a pre-built faceapi.TinyFaceDetectorOptions; a
   *                       sensible default is used if omitted.
   */
  async function sampleBlinks(videoEl, durationMs, options) {
    const opts = options || {};
    const onTick = opts.onTick || function () {};
    const shouldAbort = opts.shouldAbort || function () { return false; };
    const tinyOptions = opts.tinyOptions || new faceapi.TinyFaceDetectorOptions({ inputSize: 224 });

    const tracker = createTracker();
    let lastOpenDescriptor = null;
    const deadline = Date.now() + durationMs;

    while (Date.now() < deadline) {
      if (shouldAbort()) break;

      const result = await faceapi.detectSingleFace(videoEl, tinyOptions).withFaceLandmarks();

      if (result) {
        const ear = (eyeAspectRatio(result.landmarks.getLeftEye()) + eyeAspectRatio(result.landmarks.getRightEye())) / 2;
        const state = tracker.update(ear);

        if (state.isOpen && (!lastOpenDescriptor || Math.random() < DESCRIPTOR_REFRESH_CHANCE)) {
          try {
            const withDesc = await faceapi.detectSingleFace(videoEl, tinyOptions).withFaceLandmarks().withFaceDescriptor();
            if (withDesc) lastOpenDescriptor = withDesc.descriptor;
          } catch (e) { /* keep the previous descriptor if this particular grab fails */ }
        }

        const ceilingText = state.ceiling !== null ? `EAR ${ear.toFixed(2)} / ceiling ${state.ceiling.toFixed(2)}` : 'reading…';
        onTick(`Blink naturally… (${ceilingText} · ${state.blinkCount} blink${state.blinkCount === 1 ? '' : 's'} detected)`);
      } else {
        onTick('No face detected — make sure your face is clearly visible.');
      }

      await new Promise((r) => setTimeout(r, POLL_GAP_MS));
    }

    const result = tracker.finalize();
    return Object.assign({}, result, { descriptor: lastOpenDescriptor });
  }

  global.ExamProctoringLiveness = { eyeAspectRatio, createTracker, sampleBlinks };
})(window);
