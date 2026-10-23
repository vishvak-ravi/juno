import assert from "node:assert/strict";
import { test } from "node:test";

import { activity, describe, keyOf, replaySteps } from "../src/renderer/activity.mjs";

// Inputs one tenth of a second apart from `start`, unless they carry their own timestamp.
const inputs = (start, ...list) => list.map((input, n) => ({ timestamp: start + n * 0.1, ...input }));
const press = (key) => ({ type: "key_press", key });
const release = (key) => ({ type: "key_release", key });
const typing = (text) => [...text].map((c) => press(c === " " ? "Key.space" : c));
const scroll = (dy, dx = 0) => ({ type: "mouse_scroll", x: null, y: null, dx, dy });
const click = (button = "Button.left") => ({ type: "mouse_down", button, x: null, y: null });
const event = (id, timestamp, list, fields = {}) => ({ id, device_id: "laptop", timestamp, inputs: list, ...fields });
const words = (items) => items.map((item) => {
  const d = describe(item);
  return [d.what, d.text ?? d.keys?.join("+") ?? null, d.detail ?? null].filter((part) => part != null).join(" ");
});

test("keystrokes read as the text they typed, corrections applied, ended by Enter", () => {
  const items = activity([event("e1", 0, inputs(0, ...typing("helo"), press("Key.backspace"), ...typing("lo"),
                                                  press("Key.enter"), ...typing("next")))]);
  assert.deepEqual(words(items), ["Typed hello 1 correction", "Typed next"]);
  assert.equal(describe(items[0]).submitted, true);
  assert.equal(describe(items[1]).submitted, false);
});

test("typed text goes on across events and scrolls, and ends at a pause or a click", () => {
  const items = activity([
    event("e1", 0, inputs(0, ...typing("one "))),
    event("e2", 1, [...inputs(1, scroll(-1)), ...inputs(2, ...typing("two"))]),
    event("e3", 30, inputs(30, ...typing("later"), click(), ...typing("after"))),
  ]);
  assert.deepEqual(words(items), ["Typed one two", "Scrolled down 1 step", "Typed later", "Click", "Typed after"]);
  assert.deepEqual(items[0].events, ["e1", "e2"]);
});

test("a held modifier makes a shortcut; repeats of it count up; Shift with a character is the character", () => {
  const items = activity([event("e1", 0, inputs(0,
    press("Key.ctrl"), press("c"), release("Key.ctrl"),
    press("Key.cmd"), press("Key.tab"), press("Key.tab"), press("Key.tab"), release("Key.cmd"),
    press("Key.shift"), press("H"), press("i"), release("Key.shift"),
    press("Key.shift"), press("Key.tab"), release("Key.shift")))]);
  assert.deepEqual(words(items), ["Shortcut Ctrl+C", "Shortcut Cmd+Tab ×3", "Typed Hi", "Shortcut Shift+Tab"]);
});

test("a modifier whose release was lost stops being held", () => {
  const items = activity([event("e1", 0, [
    { ...press("Key.ctrl"), timestamp: 0 }, { ...press("c"), timestamp: 0.1 }, { ...press("x"), timestamp: 10 }])]);
  assert.deepEqual(words(items), ["Shortcut Ctrl+C", "Typed x"]);
});

test("Cmd pressed alone is a key; Shift or Ctrl pressed alone is nothing", () => {
  const items = activity([event("e1", 0, inputs(0, press("Key.cmd"), release("Key.cmd"),
                                                  press("Key.shift"), release("Key.shift")))]);
  assert.deepEqual(words(items), ["Key Cmd"]);
});

test("named keys in a row read as one item, repeats counted", () => {
  const items = activity([event("e1", 0, inputs(0, press("Key.left"), press("Key.left"), press("Key.down"), press("Key.esc")))]);
  assert.deepEqual(words(items), ["Keys ← ×2+↓+Esc"]);
});

test("scrolls in a row read as one, by direction and steps", () => {
  const items = activity([event("e1", 0, [...inputs(0, scroll(-1), scroll(-2), scroll(1)), ...inputs(10, scroll(3))])]);
  assert.deepEqual(words(items), ["Scrolled down and up 4 steps", "Scrolled up 3 steps"]);
});

test("two clicks of one button close together are a double click", () => {
  const items = activity([event("e1", 0, inputs(0, click(), click(), click("Button.right")))]);
  assert.deepEqual(words(items), ["Double click", "Click right"]);
});

test("each device's inputs are read apart, then merged in time order", () => {
  const items = activity([
    event("e1", 0, inputs(0, ...typing("ab"))),
    { ...event("t1", 0.05, inputs(0.05, ...typing("xy"))), device_id: "tablet" },
  ]);
  assert.deepEqual(items.map((i) => `${i.device_id} ${i.text}`), ["laptop ab", "tablet xy"]);
});

test("keys read as what they are", () => {
  assert.deepEqual(keyOf("Key.ctrl_r"), { modifier: "Ctrl" });
  assert.deepEqual(keyOf("Key.space"), { char: " " });
  assert.deepEqual(keyOf("Key.page_down"), { name: "Page Down" });
  assert.deepEqual(keyOf("Key.f12"), { name: "F12" });
  assert.deepEqual(keyOf("<116>"), { name: "Key 116" });
  assert.deepEqual(keyOf("\u0003"), { char: "c" });
});

test("a replay steps through an event's items at their offsets, a click or scroll where it happened", () => {
  const e = event("e1", 100, [{ type: "mouse_down", timestamp: 100.5, button: "Button.left", x: 640, y: 400 },
                             ...inputs(102, ...typing("hi"))]);
  const steps = replaySteps(e);
  assert.deepEqual(steps.map((s) => [s.offset.toFixed(1), s.kind, s.x, s.y, s.words.what]),
                   [["0.5", "click", 640, 400, "Click"], ["2.0", "typed", null, null, "Typed"]]);
});
