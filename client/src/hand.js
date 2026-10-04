// Camera → MediaPipe gesture recognition → keypoint overlay.
// A *shape* is what the hand looks like (MediaPipe's category name); a *role*
// is what Juno does with it. Only this table ties the two together.
import {
  FilesetResolver,
  GestureRecognizer,
} from "../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs";

const ROLES = { Pointing_Up: "dictate", Closed_Fist: "send" };
const COLORS = { dictate: "#00e5ff", send: "#ff3b30", none: "#888" };

const video = document.getElementById("camera");
const canvas = document.getElementById("overlay");
const label = document.getElementById("hand-label");
const context = canvas.getContext("2d");

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
  label.textContent = `shape: ${shape} · role: ${role}`;
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
