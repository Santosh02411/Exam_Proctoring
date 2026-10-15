/* Shared blink-based liveness check (eye-aspect-ratio over face-api.js
 * landmarks), used both at ID enrollment (student/enroll_face.html) and for
 * random in-exam identity spot checks (proctor.js's runSpotCheck). Pulled
 * out into one file on purpose: this logic used to be copy-pasted into both
 * places separately, and both copies had the same real bug — a fixed,
 * absolute EAR threshold (0.22) that assumes every person's eyes, camera
 * angle, and lighting produce the same "closed" number. For plenty of
 * real setups they don't, so blinks were going undetected no matter how
 * deliberately someone blinked. A single shared copy means there's only
 * one place left to get this right, and one place to fix if it's ever
 * wrong again.
 *
 * Fixes applied here:
 *  - The threshold is now RELATIVE to a rolling baseline of this person's
 *    own recent open-eye EAR, not a hardcoded constant — it adapts to
 *    whoever is actually on camera instead of assuming everyone's eyes
 *    measure the same.
 *  - The expensive 128-d face descriptor is no longer computed on every
 *    single polled frame — only landmarks (far cheaper) are computed in
 *    the hot loop, with a descriptor grabbed occasionally on a good
 *    open-eye frame. On slower hardware this alone roughly doubles (or
 *    more) how many frames get sampled in the check window, which
 *    directly improves the odds of ever sampling the instant eyes are
 *    actually closed during a real, natural blink.
 *  - The live EAR value is surfaced in the on-screen prompt, so "nothing
 *    is happening" is distinguishable from "it's working, just hasn't
 *    seen a dip yet" — both for the person taking the check and for
 *    anyone troubleshooting it afterward.
 */
(function (global) {
  const EAR_BLINK_RATIO = 0.72; // a blink = EAR drops to <=72% of this person's own recent open-eye baseline
  const EAR_FALLBACK_THRESHOLD = 0.24; // only used for the very first sampled frame, before any baseline exists
  const BASELINE_WINDOW = 8; // how many recent open-eye samples the rolling baseline is averaged over
  const DESCRIPTOR_REFRESH_CHANCE = 0.3; // re-grab the (expensive) descriptor on this fraction of good open-eye frames
  const POLL_GAP_MS = 80;

  function eyeAspectRatio(eye) {
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const vertical1 = dist(eye[1], eye[5]);
    const vertical2 = dist(eye[2], eye[4]);
    const horizontal = dist(eye[0], eye[3]);
    return horizontal ? (vertical1 + vertical2) / (2 * horizontal) : 0;
  }

  /**
   * Polls `videoEl` for up to `durationMs`, looking for at least one
   * natural blink (a dip in eye-aspect-ratio below this person's own
   * recent baseline, followed by a return to open). Returns
   * { live, blinkCount, descriptor } — `descriptor` is the 128-d face
   * descriptor from the best available open-eye frame, or null if no
   * face was ever confidently detected open.
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

    let blinkCount = 0;
    let eyesWereClosed = false;
    let lastOpenDescriptor = null;
    let baselineSamples = [];
    let baselineEAR = null;
    const deadline = Date.now() + durationMs;

    while (Date.now() < deadline) {
      if (shouldAbort()) break;

      const result = await faceapi.detectSingleFace(videoEl, tinyOptions).withFaceLandmarks();

      if (result) {
        const ear = (eyeAspectRatio(result.landmarks.getLeftEye()) + eyeAspectRatio(result.landmarks.getRightEye())) / 2;
        const threshold = baselineEAR !== null ? baselineEAR * EAR_BLINK_RATIO : EAR_FALLBACK_THRESHOLD;

        if (ear < threshold) {
          eyesWereClosed = true;
        } else {
          if (eyesWereClosed) blinkCount += 1;
          eyesWereClosed = false;

          baselineSamples.push(ear);
          if (baselineSamples.length > BASELINE_WINDOW) baselineSamples.shift();
          baselineEAR = baselineSamples.reduce((a, b) => a + b, 0) / baselineSamples.length;

          if (!lastOpenDescriptor || Math.random() < DESCRIPTOR_REFRESH_CHANCE) {
            try {
              const withDesc = await faceapi.detectSingleFace(videoEl, tinyOptions).withFaceLandmarks().withFaceDescriptor();
              if (withDesc) lastOpenDescriptor = withDesc.descriptor;
            } catch (e) { /* keep the previous descriptor if this particular grab fails */ }
          }
        }

        const earText = baselineEAR !== null ? `EAR ${ear.toFixed(2)}` : 'reading…';
        onTick(`Blink naturally… (${earText} · ${blinkCount} blink${blinkCount === 1 ? '' : 's'} detected)`);
      } else {
        onTick('No face detected — make sure your face is clearly visible.');
      }

      await new Promise((r) => setTimeout(r, POLL_GAP_MS));
    }

    return { live: blinkCount >= 1, blinkCount, descriptor: lastOpenDescriptor };
  }

  global.ExamProctoringLiveness = { eyeAspectRatio, sampleBlinks };
})(window);
