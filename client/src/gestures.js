// Event gestures: movements over several frames, as opposed to the per-frame
// shape MediaPipe names. A flick is the index fingertip moving quickly up or
// down relative to its knuckle while the other three fingers are curled, so
// moving the whole hand is not a flick.

// MediaPipe hand landmark indices.
const WRIST = 0;
const INDEX_MCP = 5;
const INDEX_TIP = 8;
const MIDDLE_MCP = 9;
const CURLED = [
  [12, 10], // middle tip, PIP
  [16, 14], // ring
  [20, 18], // pinky
];

// How far the fingertip must travel, in hand sizes (wrist to middle knuckle),
// within FLICK_WINDOW_MS to count as a flick.
const FLICK_DISTANCE = 0.5;
const FLICK_WINDOW_MS = 200;
// After a flick, ignore movement this long so the finger's return stroke is
// not read as a flick the other way.
const FLICK_COOLDOWN_MS = 500;

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

// A finger is curled when its tip is closer to the wrist than its middle joint.
function onlyIndexOut(hand) {
  return CURLED.every(([tip, pip]) => distance(hand[tip], hand[WRIST]) < distance(hand[pip], hand[WRIST]));
}

// Returns detect(hand, now): hand is one hand's landmarks (or undefined when no
// hand is seen) and now is in milliseconds; it returns "flick up",
// "flick down" or null for that frame.
export function flickDetector() {
  let samples = []; // {t, y}: fingertip height above its knuckle, in hand sizes; image y grows downward
  let quietUntil = 0;

  return function detect(hand, now) {
    if (!hand || !onlyIndexOut(hand) || now < quietUntil) {
      samples = [];
      return null;
    }
    const y = (hand[INDEX_TIP].y - hand[INDEX_MCP].y) / distance(hand[WRIST], hand[MIDDLE_MCP]);
    samples = samples.filter(({ t }) => now - t <= FLICK_WINDOW_MS);
    samples.push({ t: now, y });
    const ys = samples.map((sample) => sample.y);
    let gesture = null;
    if (y - Math.max(...ys) <= -FLICK_DISTANCE) gesture = "flick up";
    else if (y - Math.min(...ys) >= FLICK_DISTANCE) gesture = "flick down";
    if (gesture) {
      samples = [];
      quietUntil = now + FLICK_COOLDOWN_MS;
    }
    return gesture;
  };
}
