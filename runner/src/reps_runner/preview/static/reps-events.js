/* <reps-events>: one run's event stream, rendered from parsed envelopes.
 *
 * This element is the embeddable part of the REPS preview; adb-site mounts it
 * later in place of its own stream, so its surface is fixed here:
 *
 *   append(records)        parsed envelopes ({v, ts, run, experiment, schema, seq,
 *                          event}); rendered in seq order, kept across calls.
 *   replace(seq, record)   swap one record for a fuller one.
 *   clear()
 *   streaming (attribute)  shows a live indicator while set.
 *   "expand" (DOM event)   detail {seq}; dispatched when a user opens a record
 *                          the element knows is elided (the record carries
 *                          `elided: true`, set by the transport, never by the
 *                          wire vocabulary). The host fetches and calls replace().
 *
 * Theming: CSS custom properties on the host element, defaults below:
 *   --reps-bg, --reps-fg, --reps-muted, --reps-accent, --reps-mono
 *
 * The element never fetches, never reads `location`: hosts are the transports.
 * Everything from a record is inserted as text, never as markup.
 */

const STYLE = `
:host {
  --reps-bg: #ffffff;
  --reps-fg: #1b1b1b;
  --reps-muted: #6b6b6b;
  --reps-accent: #2a6fdb;
  --reps-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  display: block;
  background: var(--reps-bg);
  color: var(--reps-fg);
  font-family: var(--reps-mono);
  font-size: 12.5px;
  line-height: 1.45;
}
@media (prefers-color-scheme: dark) {
  :host {
    --reps-bg: #141517;
    --reps-fg: #e6e6e6;
    --reps-muted: #9a9a9a;
    --reps-accent: #6ea2ff;
  }
}
* { box-sizing: border-box; }
.list { margin: 0; padding: 0; list-style: none; }
.row { border-bottom: 1px solid color-mix(in srgb, var(--reps-muted) 25%, transparent); }
.head {
  display: grid;
  grid-template-columns: 4.5em 6.5em 11em minmax(0, 1fr);
  gap: 0.75em;
  padding: 0.3em 0.6em;
  cursor: pointer;
  white-space: nowrap;
}
.head:hover { background: color-mix(in srgb, var(--reps-accent) 8%, transparent); }
.row.open > .head { background: color-mix(in srgb, var(--reps-accent) 12%, transparent); }
.seq, .time { color: var(--reps-muted); text-align: right; }
.type { color: var(--reps-accent); overflow: hidden; text-overflow: ellipsis; }
.summary { overflow: hidden; text-overflow: ellipsis; }
.row.error .summary { color: #c0392b; }
.row.elided .summary::after { content: " (elided)"; color: var(--reps-muted); }
.body { padding: 0.4em 0.6em 0.8em 5.85em; }
pre {
  margin: 0; padding: 0.5em 0.75em;
  background: color-mix(in srgb, var(--reps-muted) 10%, transparent);
  border-radius: 4px;
  white-space: pre-wrap; word-break: break-word;
  max-height: 60vh; overflow: auto;
}
.message { margin: 0 0 0.6em 0; }
.message .role {
  display: inline-block; margin: 0 0 0.2em 0;
  font-weight: 600; text-transform: uppercase; font-size: 0.85em; letter-spacing: 0.04em;
  color: var(--reps-muted);
}
.message.assistant .role { color: var(--reps-accent); }
.message .text { margin: 0; white-space: pre-wrap; word-break: break-word; }
.message .reasoning { opacity: 0.75; font-style: italic; }
.message .label { color: var(--reps-muted); font-size: 0.85em; margin: 0.3em 0 0.1em 0; }
.reply { border-top: 1px dashed color-mix(in srgb, var(--reps-muted) 40%, transparent); padding-top: 0.5em; }
.meta { color: var(--reps-muted); margin: 0 0 0.5em 0; }
.toggle {
  font: inherit; color: var(--reps-accent); background: none; border: 0; padding: 0;
  cursor: pointer; text-decoration: underline;
}
.live { display: none; padding: 0.4em 0.6em; color: var(--reps-muted); }
:host([streaming]) .live { display: block; }
.live::before {
  content: ""; display: inline-block; width: 0.55em; height: 0.55em; border-radius: 50%;
  background: #2ecc71; margin-right: 0.5em; animation: reps-pulse 1.2s ease-in-out infinite;
}
@keyframes reps-pulse { 50% { opacity: 0.25; } }
.empty { padding: 0.6em; color: var(--reps-muted); }
`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function firstLine(text) {
  const line = String(text ?? "").trim().split("\n")[0];
  return line.length > 160 ? line.slice(0, 157) + "…" : line;
}

function stamp(ms) {
  return (ms / 1000).toFixed(3) + "s";
}

/* Message content is a string or a list of typed blocks; text and reasoning are
 * shown as text, other block kinds by their type. */
function contentParts(content) {
  if (typeof content === "string") return [{ kind: "text", text: content }];
  if (!Array.isArray(content)) return [];
  return content.map((block) => {
    if (!block || typeof block !== "object") return { kind: "other", text: String(block) };
    if (block.type === "text") return { kind: "text", text: block.text ?? "" };
    if (block.type === "reasoning") return { kind: "reasoning", text: block.reasoning ?? "" };
    return { kind: "other", text: `[${block.type ?? "content"}]` };
  });
}

function textOf(content) {
  return contentParts(content).filter((p) => p.kind === "text").map((p) => p.text).join("\n");
}

function replyMessage(event) {
  const choices = event.output && Array.isArray(event.output.choices) ? event.output.choices : [];
  return choices.length ? choices[0].message : null;
}

function summarize(record) {
  const event = record.event || {};
  const type = event.type;
  switch (type) {
    case "run.start":
      return `${record.experiment} · condition ${String(event.condition ?? "").slice(0, 12)} · seed ${event.seed}`;
    case "run.end":
      return `${event.state} · ${Number(event.duration_s ?? 0).toFixed(1)}s · exit ${event.exit_code}`;
    case "result":
      return `${event.name} = ${JSON.stringify(event.value)}`;
    case "status":
      return firstLine(event.detail);
    case "log":
      return `${event.level ?? "info"}: ${firstLine(event.message)}`;
    case "stdout":
    case "stderr":
      return firstLine(event.line);
    case "llm.call": {
      const head = [event.model, event.agent].filter(Boolean).join(" · ");
      if (event.error) return `${head} · error: ${firstLine(event.error)}`;
      const reply = replyMessage(event);
      const text = reply ? textOf(reply.content) : (event.output && event.output.completion) || "";
      const calls = reply && Array.isArray(reply.tool_calls) && reply.tool_calls.length
        ? ` · ${reply.tool_calls.map((c) => c.function).join(", ")}()` : "";
      return `${head} · ${firstLine(text) || "(no text)"}${calls}`;
    }
    case "custom":
      return `${event.kind} · ${keysOf(event.data)}`;
    default:
      return keysOf(event);
  }
}

function keysOf(value) {
  if (!value || typeof value !== "object") return "";
  return Object.keys(value).filter((k) => k !== "type").join(", ");
}

function renderMessage(message, extraClass) {
  const role = message && message.role ? message.role : "message";
  const box = el("div", `message ${role}${extraClass ? " " + extraClass : ""}`);
  const title = role + (message && message.function ? ` · ${message.function}` : "");
  box.appendChild(el("div", "role", title));
  for (const part of contentParts(message ? message.content : "")) {
    box.appendChild(el("p", `text ${part.kind}`, part.text));
  }
  if (message && Array.isArray(message.tool_calls) && message.tool_calls.length) {
    box.appendChild(el("div", "label", "tool calls"));
    box.appendChild(el("pre", "", pretty(message.tool_calls)));
  }
  if (message && message.error) {
    box.appendChild(el("div", "label", "tool error"));
    box.appendChild(el("pre", "", pretty(message.error)));
  }
  return box;
}

function renderTranscript(record) {
  const event = record.event;
  const box = el("div", "transcript");
  const meta = [`model ${event.model}`];
  if (event.output && event.output.model && event.output.model !== event.model) meta.push(`served ${event.output.model}`);
  if (event.agent) meta.push(`agent ${event.agent}`);
  if (event.output && event.output.usage) {
    const u = event.output.usage;
    meta.push(`tokens ${u.input_tokens ?? 0}→${u.output_tokens ?? 0}`);
  }
  if (typeof event.working_time === "number") meta.push(`${event.working_time.toFixed(2)}s`);
  box.appendChild(el("div", "meta", meta.join(" · ")));
  for (const message of Array.isArray(event.input) ? event.input : []) {
    box.appendChild(renderMessage(message));
  }
  const replyBox = el("div", "reply");
  if (event.error) {
    replyBox.appendChild(el("div", "label", "error"));
    replyBox.appendChild(el("pre", "", String(event.error)));
  }
  const reply = replyMessage(event);
  if (reply) {
    replyBox.appendChild(renderMessage(reply, "reply-message"));
    const choice = event.output.choices[0];
    if (choice.stop_reason && choice.stop_reason !== "stop") {
      replyBox.appendChild(el("div", "label", `stop reason: ${choice.stop_reason}`));
    }
  } else if (!event.error) {
    replyBox.appendChild(el("div", "label", "no reply"));
  }
  box.appendChild(replyBox);
  return box;
}

class RepsEvents extends HTMLElement {
  constructor() {
    super();
    this._rows = new Map(); // seq -> {record, row}
    this._firstTs = null;
    const root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);
    this._list = el("ol", "list");
    this._empty = el("div", "empty", "no events");
    this._live = el("div", "live", "live");
    root.appendChild(this._empty);
    root.appendChild(this._list);
    root.appendChild(this._live);
    this._list.addEventListener("click", (ev) => {
      const head = ev.target.closest(".head");
      if (!head) return;
      this._toggle(head.parentElement);
    });
  }

  get size() {
    return this._rows.size;
  }

  append(records) {
    if (!Array.isArray(records)) records = [records];
    for (const record of records) {
      if (!record || typeof record.seq !== "number") continue;
      if (this._rows.has(record.seq)) continue;  // a transport may re-send what it has shown
      const row = this._buildRow(record);
      const entry = { record, row };
      this._rows.set(record.seq, entry);
      this._insert(entry);
    }
    this._refreshTimes();
    this._empty.hidden = this._rows.size > 0;
  }

  replace(seq, record) {
    const entry = this._rows.get(seq);
    if (!entry) return this.append([record]);
    this._update(entry, { ...record, seq });
  }

  clear() {
    this._rows.clear();
    this._firstTs = null;
    this._list.replaceChildren();
    this._empty.hidden = false;
  }

  _insert(entry) {
    let next = null;
    for (const child of this._list.children) {
      if (Number(child.dataset.seq) > entry.record.seq) { next = child; break; }
    }
    this._list.insertBefore(entry.row, next);
  }

  _buildRow(record) {
    const row = el("li", "row");
    row.dataset.seq = String(record.seq);
    const head = el("div", "head");
    head.appendChild(el("span", "seq", String(record.seq)));
    head.appendChild(el("span", "time", ""));
    head.appendChild(el("span", "type", ""));
    head.appendChild(el("span", "summary", ""));
    row.appendChild(head);
    this._fillHead(row, record);
    return row;
  }

  _fillHead(row, record) {
    const event = record.event || {};
    const type = event.type === "custom" && event.kind ? `custom ${event.kind}` : String(event.type ?? "?");
    row.querySelector(".type").textContent = type;
    row.querySelector(".summary").textContent = summarize(record);
    row.classList.toggle("error", Boolean(event.error) || (event.type === "log" && event.level === "error"));
    row.classList.toggle("elided", record.elided === true);
  }

  _update(entry, record) {
    entry.record = record;
    this._fillHead(entry.row, record);
    if (entry.row.classList.contains("open")) this._renderBody(entry);
  }

  _refreshTimes() {
    let first = null;
    for (const [, entry] of this._rows) {
      const t = Date.parse(entry.record.ts);
      if (!Number.isNaN(t) && (first === null || t < first)) first = t;
    }
    if (first === this._firstTs && first !== null) {
      // only new rows need their time; the base has not moved
      for (const child of this._list.children) {
        const cell = child.querySelector(".time");
        if (!cell.textContent) this._setTime(child, first);
      }
      return;
    }
    this._firstTs = first;
    for (const child of this._list.children) this._setTime(child, first);
  }

  _setTime(row, first) {
    const entry = this._rows.get(Number(row.dataset.seq));
    const t = Date.parse(entry.record.ts);
    row.querySelector(".time").textContent =
      first === null || Number.isNaN(t) ? "" : "+" + stamp(t - first);
  }

  _toggle(row) {
    const entry = this._rows.get(Number(row.dataset.seq));
    if (!entry) return;
    const open = row.classList.toggle("open");
    if (open) {
      this._renderBody(entry);
      if (entry.record.elided === true) {
        this.dispatchEvent(new CustomEvent("expand", { detail: { seq: entry.record.seq }, bubbles: true }));
      }
    } else {
      const body = row.querySelector(".body");
      if (body) body.remove();
    }
  }

  _renderBody(entry, raw = false) {
    const old = entry.row.querySelector(".body");
    if (old) old.remove();
    const body = el("div", "body");
    const event = entry.record.event || {};
    if (event.type === "llm.call" && !raw) {
      const bar = el("div", "meta");
      const toggle = el("button", "toggle", "raw json");
      toggle.addEventListener("click", (ev) => { ev.stopPropagation(); this._renderBody(entry, true); });
      bar.appendChild(toggle);
      body.appendChild(bar);
      body.appendChild(renderTranscript(entry.record));
    } else {
      if (event.type === "llm.call") {
        const bar = el("div", "meta");
        const toggle = el("button", "toggle", "transcript");
        toggle.addEventListener("click", (ev) => { ev.stopPropagation(); this._renderBody(entry, false); });
        bar.appendChild(toggle);
        body.appendChild(bar);
      }
      body.appendChild(el("pre", "", pretty(entry.record)));
    }
    entry.row.appendChild(body);
  }
}

if (!customElements.get("reps-events")) customElements.define("reps-events", RepsEvents);

export { RepsEvents };
