// Camera → MediaPipe gesture recognition → keypoint overlay → dictation and send.
// A *shape* is what the hand looks like (MediaPipe's category name); a *role*
// is what Juno does with it. Only this table ties the two together.
// A *gesture* is a movement over several frames (gestures.js); gestures are
// shown in the overlay and drive no role.
// While the "dictate" role is held, speech is appended to the message box;
// entering the "send" role submits it.
// All of it runs only with the passive agent module on (window.juno.passiveAgent,
// the app's setting when it started): off, the preview is hidden and nothing listens.
// Minimizing the preview hides it only: the camera, the recognizer and the
// roles keep running, and the chip left in its place shows the role in effect.
import {
  FilesetResolver,
  GestureRecognizer,
} from "../../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs";
import { flickDetector } from "./gestures.js";

const ROLES = { Pointing_Up: "dictate", Closed_Fist: "send" };
const COLORS = { dictate: "#00e5ff", send: "#ff3b30", none: "#888" };
const ROLE_NAMES = { dictate: "Dictating", send: "Sending", none: "Camera" };
// Frames in a row a role must be seen before it takes effect.
const HOLD_FRAMES = 4;
// How long the overlay keeps showing the last gesture.
const GESTURE_SHOWN_MS = 1000;
// Where whether the preview is minimized is remembered, per computer.
const MINIMIZED_KEY = "juno.cameraMinimized";

const hand = document.getElementById("hand");
const video = document.getElementById("camera");
const canvas = document.getElementById("overlay");
const label = document.getElementById("hand-label");
const roleName = document.getElementById("camera-role");
const context = canvas.getContext("2d");

// The chat's message form, there while a chat is open in the detail pane; its
// New button opens an empty chat.
const chatForm = () => document.querySelector("#detail form");
function composer() {
  if (!chatForm()) document.getElementById("new-chat")?.click();
  const form = chatForm();
  return { form, input: form.querySelector("input") };
}

function minimize(on) {
  hand.classList.toggle("minimized", on);
  try {
    localStorage.setItem(MINIMIZED_KEY, on ? "1" : "0");
  } catch {}
}
function wasMinimized() {
  try {
    return localStorage.getItem(MINIMIZED_KEY) === "1";
  } catch {
    return false;
  }
}

let active = "none"; // the role in effect
let candidate = "none"; // the role seen in the latest frames
let seen = 0; // how many frames in a row
let base = ""; // the message box when the open dictation segment began
let segmentOpen = false; // a segment has started and its final text is not in yet
let sendWaiting = false; // send was asked for while a segment was still open
const detectFlick = flickDetector();
let gesture = null; // the last gesture seen
let gestureAt = 0; // when it was seen

function onDictation({ text, final }) {
  if (text) composer().input.value = base ? `${base} ${text}` : text;
  if (!final) return;
  segmentOpen = false;
  if (sendWaiting) chatForm()?.requestSubmit();
  sendWaiting = false;
}

function enter(role) {
  if (active === "dictate") window.juno.dictate(false);
  active = role;
  roleName.textContent = ROLE_NAMES[role];
  hand.style.setProperty("--active", COLORS[role]);
  if (role === "dictate") {
    base = composer().input.value.trim();
    segmentOpen = true;
    window.juno.dictate(true);
  } else if (role === "send") {
    if (segmentOpen) sendWaiting = true;
    else chatForm()?.requestSubmit();
  }
}

// Starts the recognizer and the camera and keeps the overlay drawn, one frame
// at a time. What stops either from starting is written in the overlay's label.
async function start() {
  window.juno.onDictation(onDictation);
  let recognizer;
  try {
    recognizer = await GestureRecognizer.createFromOptions(
      await FilesetResolver.forVisionTasks("../../node_modules/@mediapipe/tasks-vision/wasm"),
      {
        baseOptions: { modelAssetPath: "../../models/gesture_recognizer.task", delegate: "GPU" },
        runningMode: "VIDEO",
        numHands: 1,
      },
    );
    video.srcObject = await navigator.mediaDevices.getUserMedia({ video: true });
    await video.play();
  } catch (error) {
    label.textContent = `hand tracking: ${error.message || error}`;
    return;
  }
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;

  function onFrame() {
    const now = performance.now();
    const result = recognizer.recognizeForVideo(video, now);
    const shape = result.gestures[0]?.[0].categoryName ?? "no hand";
    const role = ROLES[shape] ?? "none";
    seen = role === candidate ? seen + 1 : 1;
    candidate = role;
    if (seen === HOLD_FRAMES && role !== active) enter(role);
    const flick = detectFlick(result.landmarks[0], now);
    if (flick) [gesture, gestureAt] = [flick, now];
    const shown = gesture && now - gestureAt < GESTURE_SHOWN_MS ? gesture : "none";
    label.textContent = `shape: ${shape} · role: ${role} · active: ${active} · gesture: ${shown}`;
    label.style.color = COLORS[role];
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = COLORS[role];
    for (const { x, y } of result.landmarks[0] ?? []) {
      context.beginPath();
      context.arc(x * canvas.width, y * canvas.height, 6, 0, 2 * Math.PI);
      context.fill();
    }
    video.requestVideoFrameCallback(onFrame);
  }
  video.requestVideoFrameCallback(onFrame);
}

if (window.juno?.passiveAgent) {
  document.getElementById("camera-hide").onclick = () => minimize(true);
  document.getElementById("camera-show").onclick = () => minimize(false);
  minimize(wasMinimized());
  await start();
} else {
  hand.hidden = true;
}
