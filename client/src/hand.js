// Camera → MediaPipe gesture recognition → keypoint overlay → dictation and send.
// A *shape* is what the hand looks like (MediaPipe's category name); a *role*
// is what Juno does with it. Only this table ties the two together.
// While the "dictate" role is held, speech is appended to the message box;
// entering the "send" role submits it.
import {
  FilesetResolver,
  GestureRecognizer,
} from "../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs";

const ROLES = { Pointing_Up: "dictate", Closed_Fist: "send" };
const COLORS = { dictate: "#00e5ff", send: "#ff3b30", none: "#888" };
// Frames in a row a role must be seen before it takes effect.
const HOLD_FRAMES = 4;

const video = document.getElementById("camera");
const canvas = document.getElementById("overlay");
const label = document.getElementById("hand-label");
const context = canvas.getContext("2d");

// The page's message form: index.html has one always; browse.html has one
// while a chat is open, and its New button opens an empty chat.
function composer() {
  if (!document.querySelector("form")) document.getElementById("new-chat")?.click();
  const form = document.querySelector("form");
  return { form, input: form.querySelector("input") };
}

let active = "none"; // the role in effect
let candidate = "none"; // the role seen in the latest frames
let seen = 0; // how many frames in a row
let base = ""; // the message box when the open dictation segment began
let segmentOpen = false; // a segment has started and its final text is not in yet
let sendWaiting = false; // send was asked for while a segment was still open

window.juno.onDictation(({ text, final }) => {
  if (text) composer().input.value = base ? `${base} ${text}` : text;
  if (!final) return;
  segmentOpen = false;
  if (sendWaiting) document.querySelector("form")?.requestSubmit();
  sendWaiting = false;
});

function enter(role) {
  if (active === "dictate") window.juno.dictate(false);
  active = role;
  if (role === "dictate") {
    base = composer().input.value.trim();
    segmentOpen = true;
    window.juno.dictate(true);
  } else if (role === "send") {
    if (segmentOpen) sendWaiting = true;
    else document.querySelector("form")?.requestSubmit();
  }
}

const recognizer = await GestureRecognizer.createFromOptions(
  await FilesetResolver.forVisionTasks("../node_modules/@mediapipe/tasks-vision/wasm"),
  {
    baseOptions: { modelAssetPath: "../models/gesture_recognizer.task", delegate: "GPU" },
    runningMode: "VIDEO",
    numHands: 1,
  },
);

video.srcObject = await navigator.mediaDevices.getUserMedia({ video: true });
await video.play();
canvas.width = video.videoWidth;
canvas.height = video.videoHeight;

function onFrame() {
  const result = recognizer.recognizeForVideo(video, performance.now());
  const shape = result.gestures[0]?.[0].categoryName ?? "no hand";
  const role = ROLES[shape] ?? "none";
  seen = role === candidate ? seen + 1 : 1;
  candidate = role;
  if (seen === HOLD_FRAMES && role !== active) enter(role);
  label.textContent = `shape: ${shape} · role: ${role} · active: ${active}`;
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
