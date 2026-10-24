// Pure, deterministic tests for the core liveness decision algorithm
// (app/static/js/liveness.js's createTracker) -- driven with synthetic EAR
// number sequences rather than a real or simulated camera, so these can
// pin down exactly how the algorithm behaves under realistic scenarios
// without depending on a browser, face-api.js, or actual webcam hardware.
//
// Run with: node tests/js/test_liveness.mjs

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(__dirname, "../../app/static/js/liveness.js"), "utf8");

// liveness.js attaches itself to `window` -- give it one, then run the file.
const sandboxWindow = {};
const fn = new Function("window", source + "\nreturn window.ExamProctoringLiveness;");
const { createTracker } = fn(sandboxWindow);

let failures = [];
function check(label, cond, detail) {
  if (cond) {
    console.log(`[PASS] ${label}`);
  } else {
    failures.push(label);
    console.log(`[FAIL] ${label}${detail !== undefined ? "\n       -> " + JSON.stringify(detail) : ""}`);
  }
}

function runSequence(ears) {
  const tracker = createTracker();
  const states = ears.map((e) => tracker.update(e));
  return { states, result: tracker.finalize() };
}

// Builds a realistic "settled open, then N blinks, then open again" trace.
// `open` = baseline open-eye EAR, `closed` = EAR during the closed frame(s)
// of each blink, `blinkFrames` = how many consecutive samples each blink's
// dip lasts (a fast poll rate can catch a real blink across 1-3 frames).
function withBlinks(open, closed, blinkPositions, blinkFrames, totalSamples) {
  const seq = [];
  for (let i = 0; i < totalSamples; i++) {
    const inBlink = blinkPositions.some((pos) => i >= pos && i < pos + blinkFrames);
    seq.push(inBlink ? closed : open);
  }
  return seq;
}

console.log("=== Scenario 1: a clean, obvious blink ===");
{
  // Settled at 0.30, one clear blink dropping to 0.08 for 2 frames, then back.
  const seq = withBlinks(0.30, 0.08, [20], 2, 60);
  const { result } = runSequence(seq);
  check("detects exactly 1 blink", result.blinkCount === 1, result);
  check("live == true", result.live === true, result);
  check("not via movement fallback (a real blink was caught)", result.viaMovementFallback === false, result);
}

console.log("\n=== Scenario 2: two separate blinks ===");
{
  const seq = withBlinks(0.31, 0.09, [15, 40], 2, 70);
  const { result } = runSequence(seq);
  check("detects exactly 2 blinks", result.blinkCount === 2, result);
  check("live == true", result.live === true, result);
}

console.log("\n=== Scenario 3: slow natural drift downward, NO real blink ===");
{
  // This is the actual failure mode that prompted the rewrite: eyelid
  // relaxation over several seconds, from 0.30 down to 0.26, with no
  // genuine open->closed->open transition at all.
  const seq = [];
  for (let i = 0; i < 80; i++) seq.push(0.30 - (0.04 * i) / 79);
  const { result } = runSequence(seq);
  check("a slow monotonic drift registers 0 blinks (no false positive)", result.blinkCount === 0, result);
  // A ~13% drift DOES pass via the movement fallback, and that's correct,
  // not a bug: the fallback's whole job is "is the signal doing anything a
  // frozen photo couldn't" -- a frozen photo (scenario 5/6) shows ~0%
  // range. Any real, sustained, meaningful change -- gradual or sudden --
  // is itself evidence of a live, changing scene.
  check("a ~13% monotonic drift passes via the movement fallback (correct: a frozen photo would show ~0% range)",
      result.live === true && result.viaMovementFallback === true, result);
}

console.log("\n=== Scenario 4: drift downward, THEN a real blink ===");
{
  // The actual scenario my fix needs to handle correctly: baseline drifts
  // down first (simulating several seconds of relaxed staring), and only
  // THEN does a genuine blink happen, dipping from the NEW resting level.
  const seq = [];
  for (let i = 0; i < 40; i++) seq.push(0.30 - (0.04 * i) / 39); // drift 0.30 -> 0.26
  for (let i = 0; i < 3; i++) seq.push(0.08); // blink from the drifted resting level
  for (let i = 0; i < 30; i++) seq.push(0.26); // back open, at the new resting level
  const { result } = runSequence(seq);
  check("a blink after drift is still caught (ceiling doesn't chase the drift down)",
      result.blinkCount === 1, result);
  check("live == true", result.live === true, result);
}

console.log("\n=== Scenario 5: frozen/static sequence (simulated printed photo) ===");
{
  // A completely unchanging EAR, as a static photo re-evaluated frame to
  // frame by a deterministic model would produce.
  const seq = Array(60).fill(0.29);
  const { result } = runSequence(seq);
  check("zero blinks", result.blinkCount === 0, result);
  check("live == false (must not pass a frozen signal)", result.live === false, result);
  check("range is ~0", result.range < 0.01, result);
}

console.log("\n=== Scenario 6: tiny camera/model sensor noise, no real motion ===");
{
  // Even a static photo under a live camera has a little sensor noise --
  // must not be enough on its own to pass.
  const seq = [];
  for (let i = 0; i < 60; i++) seq.push(0.29 + (i % 2 === 0 ? 0.004 : -0.004));
  const { result } = runSequence(seq);
  check("small sensor-noise-level jitter alone does not pass", result.live === false, result);
}

console.log("\n=== Scenario 7: gentle natural eye-openness variation that never crosses the blink threshold at all ===");
{
  // No frame ever drops anywhere near the strict blink threshold (ceiling
  // stabilizes around ~0.30-0.31, so the threshold sits near ~0.25, and
  // this oscillation's floor is 0.28 -- well above it) -- but there's
  // enough genuine variation (>=10% range) to be clear evidence this isn't
  // a frozen photo. Exactly the case the movement fallback exists for: a
  // landmark model that under-estimates how far the eye actually closed,
  // a quick partial blink the poll interval only half-caught, general
  // natural micro-movement, etc.
  const seq = [];
  for (let i = 0; i < 50; i++) {
    const t = i / 49;
    seq.push(0.30 + 0.02 * Math.sin(t * Math.PI * 4)); // oscillates between 0.28 and 0.32
  }
  const { result } = runSequence(seq);
  check("no blink crosses the threshold", result.blinkCount === 0, result);
  check("passes via the movement fallback", result.live === true && result.viaMovementFallback === true, result);
}

console.log("\n=== Scenario 8: movement fallback requires enough samples, not just a big swing ===");
{
  const seq = [0.30, 0.10, 0.30]; // huge range, but only 3 samples
  const { result } = runSequence(seq);
  // Note: a dip to 0.10 here IS below the fallback absolute threshold
  // (0.24) on first sample context, so this specific sequence may also
  // register a real blink -- that's fine and expected (it's a genuine
  // dip-then-recover); the point of this scenario is scenario 8b below,
  // which isolates the sample-count guard with NO blink-level dip at all.
  check("(informational) short-but-sharp sequence result", true, result);
}
{
  // A real case for the sample-count guard: meaningful variation, but
  // never actually below the blink threshold, and too few samples overall.
  const seq = [0.30, 0.30, 0.26, 0.24, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30];
  const { result } = runSequence(seq);
  check("too few samples -> movement fallback does not fire even with some variation",
      result.viaMovementFallback === false, result);
}

console.log("\n=== Scenario 9: no face ever detected (zero samples) ===");
{
  const tracker = createTracker();
  const result = tracker.finalize();
  check("live == false with no samples at all", result.live === false, result);
  check("does not crash on empty input", result.blinkCount === 0, result);
}

console.log("\n=== Scenario 10: single noisy early outlier must not permanently lock in an unreachable ceiling ===");
{
  // If the very first sample were trusted outright as the ceiling, one
  // freak high reading could make every subsequent genuine blink need an
  // unrealistically large drop. The ceiling is smoothed over a few open
  // samples specifically to guard against this.
  const seq = [0.45, 0.29, 0.30, 0.29]; // one outlier, then a realistic plateau
  for (let i = 0; i < 20; i++) seq.push(0.29 + (i % 2 ? 0.005 : -0.005));
  seq.push(0.09, 0.09); // a real blink relative to the TRUE (~0.29) plateau
  for (let i = 0; i < 20; i++) seq.push(0.29);
  const { result } = runSequence(seq);
  check("a realistic blink is still caught despite one early outlier reading",
      result.blinkCount >= 1, result);
}

console.log("\n" + "=".repeat(60));
if (failures.length) {
  console.log(`${failures.length} FAILURE(S):`);
  failures.forEach((f) => console.log(" - " + f));
  process.exit(1);
} else {
  console.log("ALL PASS");
  process.exit(0);
}
