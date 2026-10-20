// The markdown the model tends to write: headings, fenced code, lists,
// paragraphs; `code`, **bold** and *italic* inside them. Anything else stays text.
import { el } from "./views.js";

export function inline(node, text) {
  node.append(...text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/).map((part) =>
    part.startsWith("`") ? el("code", { textContent: part.slice(1, -1) })
    : part.startsWith("**") ? el("strong", { textContent: part.slice(2, -2) })
    : part.startsWith("*") ? el("em", { textContent: part.slice(1, -1) })
    : part));
  return node;
}

const BLOCK = /^(```|#{1,6}\s|\s*(?:[-*]|\d+\.)\s)/;

export function markdown(text) {
  const out = el("div", { className: "md" });
  const lines = text.split("\n");
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    let m;
    if (line.startsWith("```")) {
      const code = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.append(el("pre", {}, el("code", { textContent: code.join("\n") })));
    } else if ((m = line.match(/^(#{1,6})\s+(.*)/))) {
      out.append(inline(el(`h${m[1].length}`), m[2]));
      i++;
    } else if ((m = line.match(/^\s*(?:[-*]|(\d+)\.)\s/))) {
      const list = el(m[1] ? "ol" : "ul");
      for (; i < lines.length && (m = lines[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)/)); i++) list.append(inline(el("li"), m[1]));
      out.append(list);
    } else if (!line.trim()) {
      i++;
    } else {
      const para = [];
      for (; i < lines.length && lines[i].trim() && !BLOCK.test(lines[i]); i++) para.push(lines[i]);
      out.append(inline(el("p"), para.join("\n")));
    }
  }
  return out;
}
