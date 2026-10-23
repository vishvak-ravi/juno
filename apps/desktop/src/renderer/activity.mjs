// What the user did, computed from GET /events rows without drawing or
// requesting anything: every device's inputs read in time order and folded into
// items, one per typed text, shortcut, run of keys, click or scroll. Typed text
// ends at Enter, a pause, a click or any other key. Pointer moves and key and
// button releases make no item; a modifier's release ends its hold.

// An item is { kind, device_id, start, end, events, ... } with `events` the ids of
// the events whose inputs it holds, oldest first, and per kind:
//   typed     text, submitted (ended by Enter), corrections (backspaces)
//   shortcut  keys (["Ctrl", "C"]), count
//   keys      keys (["←", "←", "↓"])
//   click     button ("left"), modifiers held (["Ctrl"]), count (2 for a double click)
//   scroll    up, down, left, right (steps each way)

// When consecutive inputs belong to one item, in seconds: a pause longer than
// `typing` ends typed text, `scroll` a scroll, `keys` a run of keys or a
// repeated shortcut, `click` a double click; a modifier pressed longer than
// `modifier` before a key, with no release seen, is no longer held.
export const GAPS = { typing: 10, scroll: 3, keys: 2, click: 0.5, modifier: 3 };

const MODIFIERS = { ctrl: "Ctrl", ctrl_l: "Ctrl", ctrl_r: "Ctrl", alt: "Alt", alt_l: "Alt", alt_r: "Alt", alt_gr: "AltGr",
                    shift: "Shift", shift_l: "Shift", shift_r: "Shift", cmd: "Cmd", cmd_l: "Cmd", cmd_r: "Cmd" };
const MODIFIER_ORDER = ["Ctrl", "Alt", "AltGr", "Shift", "Cmd"];
const NAMES = { enter: "Enter", tab: "Tab", esc: "Esc", backspace: "Backspace", delete: "Delete", space: "Space",
                up: "↑", down: "↓", left: "←", right: "→", home: "Home", end: "End", page_up: "Page Up", page_down: "Page Down",
                insert: "Insert", menu: "Menu", print_screen: "Print Screen" };
// Keys that change nothing the user reads: they neither make an item nor end one.
const SILENT = new Set(["caps_lock", "num_lock", "scroll_lock"]);

// A key of an input as { modifier } , { char } or { name }: "Key.ctrl_l" is a
// modifier, "a" and "Key.space" are characters typed, "Key.left" is "←", "<116>"
// (a key with no name) is "Key 116". A control character (Ctrl held) is its letter.
export function keyOf(key) {
  if (key.startsWith("Key.")) {
    const name = key.slice(4);
    if (name in MODIFIERS) return { modifier: MODIFIERS[name] };
    if (name === "space") return { char: " " };
    if (SILENT.has(name)) return { silent: true };
    if (/^f\d+$/.test(name)) return { name: name.toUpperCase() };
    return { name: NAMES[name] ?? name.replaceAll("_", " ") };
  }
  const code = /^<(\d+)>$/.exec(key);
  if (code) return { name: `Key ${code[1]}` };
  if (key.length === 1 && key.charCodeAt(0) < 32) return { char: String.fromCharCode(key.charCodeAt(0) + 96) };
  return { char: key };
}

// What a shortcut's last key reads as: "C" for "c", the name otherwise.
const shortcutKey = (key) => key.name ?? (key.char.length === 1 ? key.char.toUpperCase() : key.char);

// The items of `events` (GET /events rows, in any order), oldest first.
export function activity(events, gaps = GAPS) {
  const byDevice = new Map();
  for (const event of events) {
    if (!byDevice.has(event.device_id)) byDevice.set(event.device_id, []);
    for (const input of event.inputs) byDevice.get(event.device_id).push({ input, event: event.id });
  }
  const items = [...byDevice].flatMap(([device, inputs]) =>
    deviceActivity(device, inputs.sort((a, b) => a.input.timestamp - b.input.timestamp), gaps));
  return items.sort((a, b) => a.start - b.start);
}

// One device's inputs, oldest first, folded into items.
function deviceActivity(device, inputs, gaps) {
  const items = [];
  const held = new Map();  // modifier -> when it was pressed
  let tapped = null;       // a modifier pressed with no key since: { name, at, event }
  const open = (kind, at, event, fields) => {
    const item = { kind, device_id: device, start: at, end: at, events: [event], ...fields };
    items.push(item);
    return item;
  };
  const extend = (item, at, event) => {
    item.end = at;
    if (item.events.at(-1) !== event) item.events.push(event);
    return item;
  };
  // The last item if it is of `kind`, still open, and ended at most `gap` before
  // `at`. Typed text goes on past scrolls, which leave the focus where it was.
  const continuing = (kind, at, gap) => {
    let n = items.length - 1;
    if (kind === "typed") while (items[n]?.kind === "scroll") n -= 1;
    const item = items[n];
    return item?.kind === kind && !item.closed && at - item.end <= gap ? item : null;
  };

  for (const { input, event } of inputs) {
    const at = input.timestamp;
    if (input.type === "key_release") {
      const key = keyOf(input.key);
      if (!key.modifier) continue;
      held.delete(key.modifier);
      // A modifier pressed and released alone, such as Cmd opening the launcher; Shift and Ctrl alone do nothing.
      if (tapped?.name === key.modifier && (key.modifier === "Cmd" || key.modifier === "Alt")) {
        const item = continuing("keys", tapped.at, gaps.keys) ?? open("keys", tapped.at, tapped.event, { keys: [] });
        extend(item, tapped.at, tapped.event).keys.push(key.modifier);
      }
      tapped = null;
      continue;
    }
    if (input.type === "key_press") {
      const key = keyOf(input.key);
      if (key.silent) continue;
      if (key.modifier) {
        held.set(key.modifier, at);
        // Held down, a modifier repeats its press; a second modifier makes it no tap.
        if (!tapped) tapped = { name: key.modifier, at, event };
        else if (tapped.name !== key.modifier) tapped = null;
        continue;
      }
      tapped = null;
      const modifiers = heldAt(held, at, gaps);
      const typing = continuing("typed", at, gaps.typing);
      // Shift with a character is the character itself, already shifted; Shift with
      // Enter or Backspace while typing is a line break or a correction.
      const typingKey = typing && (key.name === "Enter" || key.name === "Backspace");
      if (modifiers.some((m) => m !== "Shift") || (modifiers.length && key.name && !typingKey)) {
        const keys = [...modifiers, shortcutKey(key)];
        const item = continuing("shortcut", at, gaps.keys);
        if (item && item.keys.join("+") === keys.join("+")) extend(item, at, event).count += 1;
        else open("shortcut", at, event, { keys, count: 1 });
        continue;
      }
      if (key.char != null) {
        const item = typing ?? open("typed", at, event, { text: "", submitted: false, corrections: 0 });
        extend(item, at, event).text += key.char;
        continue;
      }
      if (typing && key.name === "Backspace") {
        extend(typing, at, event);
        typing.text = typing.text.slice(0, -1);
        typing.corrections += 1;
        continue;
      }
      if (typing && key.name === "Enter" && modifiers.length) {
        extend(typing, at, event).text += "\n";
        continue;
      }
      if (typing && key.name === "Enter") {
        extend(typing, at, event).submitted = true;
        typing.closed = true;
        continue;
      }
      const item = continuing("keys", at, gaps.keys) ?? open("keys", at, event, { keys: [] });
      extend(item, at, event).keys.push(key.name);
      continue;
    }
    if (input.type === "mouse_down") {
      tapped = null;
      const button = (input.button ?? "").replace(/^Button\./, "");
      const modifiers = heldAt(held, at, gaps);
      const item = continuing("click", at, gaps.click);
      if (item && item.button === button && item.modifiers.join() === modifiers.join()) extend(item, at, event).count += 1;
      else open("click", at, event, { button, modifiers, count: 1 });
      continue;
    }
    if (input.type === "mouse_scroll") {
      tapped = null;
      const item = continuing("scroll", at, gaps.scroll) ?? open("scroll", at, event, { up: 0, down: 0, left: 0, right: 0 });
      extend(item, at, event);
      // As napsack reads a scroll: dy > 0 is up, dx > 0 is right.
      if (input.dy > 0) item.up += input.dy;
      if (input.dy < 0) item.down -= input.dy;
      if (input.dx > 0) item.right += input.dx;
      if (input.dx < 0) item.left -= input.dx;
    }
  }
  return items.map(({ closed, ...item }) => item);
}

// The modifiers held at `at`, in their usual order. One pressed or last used
// longer than `gaps.modifier` ago is let go, its release taken as lost; the
// others count as used at `at`.
function heldAt(held, at, gaps) {
  for (const [modifier, since] of held) {
    if (at - since > gaps.modifier) held.delete(modifier);
    else held.set(modifier, at);
  }
  return MODIFIER_ORDER.filter((m) => held.has(m));
}

// An item in words: `what` the user did, then what with (`text` typed or `keys`
// pressed), and a `detail` where there is more to say.
//   typed     { what: "Typed", text: "hello", submitted: true, detail: "2 corrections" }
//   shortcut  { what: "Shortcut", keys: ["Ctrl", "C"], detail: "×3" }
//   keys      { what: "Keys", keys: ["← ×2", "↓"] }
//   click     { what: "Double click", keys: ["Ctrl"], detail: "right" }
//   scroll    { what: "Scrolled down", detail: "14 steps" }
export function describe(item) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  switch (item.kind) {
    case "typed": return { what: item.text.trim() ? "Typed" : "Typed and deleted", text: item.text.trim() ? item.text : null,
                           submitted: item.submitted, detail: item.corrections ? plural(item.corrections, "correction") : null };
    case "shortcut": return { what: "Shortcut", keys: item.keys, detail: item.count > 1 ? `×${item.count}` : null };
    case "keys": return { what: item.keys.length === 1 ? "Key" : "Keys", keys: runs(item.keys) };
    case "click": {
      const what = item.count === 1 ? "Click" : item.count === 2 ? "Double click" : `${item.count} clicks`;
      return { what, keys: item.modifiers.length ? item.modifiers : null, detail: item.button === "left" ? null : item.button };
    }
    case "scroll": {
      const ways = [["down", item.down], ["up", item.up], ["right", item.right], ["left", item.left]].filter(([, n]) => n);
      ways.sort((a, b) => b[1] - a[1]);
      const steps = ways.reduce((sum, [, n]) => sum + n, 0);
      return { what: ways.length ? `Scrolled ${ways.map(([way]) => way).join(" and ")}` : "Scrolled",
               detail: plural(steps, "step") };
    }
    default: return { what: item.kind };
  }
}

// ["←", "←", "↓"] → ["← ×2", "↓"].
function runs(keys) {
  const out = [];
  for (let n = 0; n < keys.length;) {
    let end = n;
    while (keys[end + 1] === keys[n]) end += 1;
    out.push(end > n ? `${keys[n]} ×${end - n + 1}` : keys[n]);
    n = end + 1;
  }
  return out;
}

// One event's inputs as the steps of a replay over its screenshot, in order:
// `{ offset, kind, x, y, words }` per item of its activity, `offset` seconds
// after the screenshot, `x` and `y` where a click or scroll happened (desktop
// pixels; null where the pointer was not read or the item is keys) and `words`
// as `describe` puts them.
export function replaySteps(event, gaps = GAPS) {
  return activity([event], gaps).map((item) => {
    const type = item.kind === "click" ? "mouse_down" : item.kind === "scroll" ? "mouse_scroll" : null;
    const input = type && event.inputs.find((i) => i.type === type && i.timestamp === item.start);
    return { offset: item.start - event.timestamp, kind: item.kind, x: input?.x ?? null, y: input?.y ?? null, words: describe(item) };
  });
}
