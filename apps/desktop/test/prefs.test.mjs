// The desktop app's own settings: defaults, what a change keeps and what it refuses.
import assert from "node:assert/strict";
import test from "node:test";

import prefs from "../src/main/prefs.js";

const { defaultsOf, describe, patched, valuesOf } = prefs;
const defaults = defaultsOf("http://10.0.0.2:8765");

test("without a saved file every setting is its default", () => {
  assert.deepEqual(valuesOf({}, defaults), defaults);
});

test("a change keeps only what differs from the defaults", () => {
  const { saved } = patched({}, defaults, { set: { "app.serverUrl": "https://juno.example/", "app.prefetch": 3 } });
  assert.deepEqual(saved, { serverUrl: "https://juno.example" });
  assert.equal(valuesOf(saved, defaults).serverUrl, "https://juno.example");
  assert.deepEqual(patched(saved, defaults, { reset: ["app.serverUrl"] }).saved, {});
});

test("a refused change names each setting and keeps nothing", () => {
  const { errors, saved } = patched({}, defaults, { set: { "app.serverUrl": "ftp://x", "app.prefetch": -1, "app.colour": 1 } });
  assert.equal(saved, undefined);
  assert.deepEqual(Object.keys(errors).sort(), ["app.colour", "app.prefetch", "app.serverUrl"]);
});

test("a saved value this version cannot read keeps the default", () => {
  assert.equal(valuesOf({ prefetch: "many" }, defaults).prefetch, defaults.prefetch);
});

test("the description lists every setting with its value and default", () => {
  const { sections } = describe(valuesOf({ prefetch: 5 }, defaults), defaults);
  const prefetch = sections[0].fields.find((f) => f.path === "app.prefetch");
  assert.deepEqual([prefetch.kind, prefetch.value, prefetch.default], ["int", 5, defaults.prefetch]);
});
