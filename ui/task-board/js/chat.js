// ─── Task assistant · linked chat ────────────────────────────────────
// The right pane of the split workspace. It opens empty — a single centered
// whisper naming what it knows — and waits for an instruction ("Tell me what
// to do…"). Under the hood it's headless Claude Code continuing this card's
// session: a POST to /api/tasks/<id>/chat opens an SSE stream, and the server
// owns the system prompt (identity + voice from profile/, never hardcoded).
// Replies read like Claude: a thinking line, inline tool steps, then streamed
// text. When a turn completes, the left task detail re-settles from real
// persisted state so any artifact edits the agent made appear.
//
// TIER-2 boundary: this chat drafts and edits task artifacts only — it NEVER
// fires an external write (send-message / publish-jira / schedule-meeting).
// Those remain the left-pane gated verbs, each behind one plain-language
// confirm (invariant #5: agent drafts → board publishes). Chat-initiated
// action proposals (an inline confirm that routes to a gated board endpoint)
// are a documented follow-on, not implemented here.
//
// Depends on globals: API, svgIcon, escapeHtml, formatDate, meetingName,
// fetchTasks (board refresh), openTask (left-detail re-render).

let chatState = null; // { task, busy }

// Reveal an element's enter-transition without relying on requestAnimationFrame
// (which is throttled when the tab isn't focused). Forcing a reflow commits the
// hidden baseline, so adding the class animates to the shown state.
function revealNow(el, cls) { void el.offsetWidth; el.classList.add(cls); }

// ── Markdown rendering for assistant prose (dependency-free, XSS-safe) ─────────
// The board has NO build step and NO libraries, so this is a small vanilla
// renderer for the common markdown subset assistant replies use.
//
// SAFETY MODEL — escape-first: every raw character from the model is
// HTML-escaped (& < >) BEFORE any tag is emitted, so the ONLY live HTML in the
// output is the handful of tags this function generates itself. Assistant text
// can never inject markup: a literal "<script>" arrives as "&lt;script&gt;".
// Links are additionally scheme-gated to http/https/mailto so a
// "[x](javascript:…)" can't produce an executable href — it degrades to plain
// text. This is safe to call on PARTIAL text mid-stream: an unclosed token like
// a lone "**" simply renders literally until its pair arrives, because we
// re-render the whole accumulated buffer on each token.
//
// Code is handled by SPLITTING, never by placeholder/sentinel substitution
// (which collides with real prose like "step 3 of 5"): fenced blocks are found
// by a line-by-line ``` toggle pass, and inline code by splitting each line on
// /(`[^`]+`)/ and formatting only the non-code segments.

function mdEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Inline formatting for a single already-context-safe line. Operates on RAW
// (un-escaped) text: it splits out inline-code spans first, then escapes +
// formats the prose between them, then escapes (no further formatting) the code.
function mdInline(line) {
  return line
    .split(/(`[^`]+`)/)
    .map(seg => {
      if (seg.length >= 2 && seg[0] === '`' && seg[seg.length - 1] === '`') {
        return `<code>${mdEscape(seg.slice(1, -1))}</code>`;
      }
      return mdFormatProse(seg);
    })
    .join('');
}

// Escape FIRST, then apply emphasis/link tags to the now-safe string. Order:
// bold (** / __) and strike (~~) before italic (* / _) so the single-char
// emphasis doesn't eat the inner chars of the double-char markers.
function mdFormatProse(seg) {
  let s = mdEscape(seg);
  // links: [label](url) — only safe schemes; otherwise emit the label as text.
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    if (/^(https?:|mailto:)/i.test(url)) {
      // url came through mdEscape already, so quotes/brackets are inert.
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
    return label; // block javascript: and other schemes — render label only
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^_])_([^_\s][^_]*?)_/g, '$1<em>$2</em>');
  return s;
}

function renderMarkdown(src) {
  const lines = String(src == null ? '' : src).split('\n');
  const out = [];
  let i = 0;
  let para = [];      // buffer of inline-rendered lines for the current paragraph
  let list = null;    // { tag: 'ul'|'ol', items: [] } for the current list

  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.join('<br>')}</p>`); para = []; }
  };
  const flushList = () => {
    if (list) { out.push(`<${list.tag}>${list.items.map(it => `<li>${it}</li>`).join('')}</${list.tag}>`); list = null; }
  };

  while (i < lines.length) {
    const raw = lines[i];

    // Fenced code block — find the ``` toggle and accumulate the body verbatim.
    const fence = raw.match(/^\s*```(.*)$/);
    if (fence) {
      flushPara(); flushList();
      i += 1;
      const body = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i += 1; }
      i += 1; // consume the closing fence (if present; end-of-input is fine)
      out.push(`<pre><code>${mdEscape(body.join('\n'))}</code></pre>`);
      continue;
    }

    // Blank line — paragraph / list break.
    if (/^\s*$/.test(raw)) { flushPara(); flushList(); i += 1; continue; }

    // Headings (#, ##, ###) → small headings.
    const h = raw.match(/^\s*(#{1,3})\s+(.*)$/);
    if (h) {
      flushPara(); flushList();
      const level = h[1].length;
      out.push(`<h${level} class="md-h">${mdInline(h[2])}</h${level}>`);
      i += 1; continue;
    }

    // Unordered list item (-, *, +).
    const ul = raw.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
      list.items.push(mdInline(ul[1]));
      i += 1; continue;
    }

    // Ordered list item (1.).
    const ol = raw.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      flushPara();
      if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
      list.items.push(mdInline(ol[1]));
      i += 1; continue;
    }

    // Plain prose line — accumulate; single newlines become <br> within a <p>.
    flushList();
    para.push(mdInline(raw));
    i += 1;
  }
  flushPara(); flushList();
  return out.join('');
}

async function buildChat(task) {
  chatState = { task, busy: false };

  const anchor = document.getElementById('chat-anchor');
  if (anchor) {
    anchor.innerHTML = `<span class="chat-anchor-dot"></span>linked to ${escapeHtml(task.id)}`;
    anchor.title = `Continuing this card's session`;
  }

  const empty = document.getElementById('chat-empty');
  if (empty) empty.classList.remove('gone');
  const thread = document.getElementById('chat-thread');
  if (thread) thread.innerHTML = '';

  // Replay any persisted history INSTANTLY so re-opening a chatted task shows
  // its prior turns rather than an empty panel. Robust to network/empty/parse
  // failure: on any problem we simply leave the empty-whisper state in place.
  if (thread) {
    try {
      const res = await fetch(`${API}/tasks/${task.id}/chat`);
      if (res.ok) {
        const data = await res.json();
        const turns = groupEventsIntoTurns((data && data.events) || []);
        if (turns.length) {
          turns.forEach(turn => thread.appendChild(renderTurn(turn, true)));
          if (empty) empty.classList.add('gone');
          scrollThread();
        }
      }
    } catch (_) { /* leave the empty-whisper state untouched */ }
  }

  const input = document.getElementById('chat-input');
  if (input) {
    input.value = '';
    input.style.height = 'auto';
    input.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } };
    input.oninput = function () { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 140) + 'px'; };
    // Land the cursor in the composer once the workspace reveal settles. The
    // chat footer fades in on a .48s delay (see index.html), so we focus just
    // after that so the blinking caret appears in an already-visible field
    // rather than behind the fade.
    setTimeout(() => {
      const a = document.activeElement;
      if (!a || a === document.body || a === input) input.focus();
    }, 520);
  }
  const send = document.getElementById('chat-send');
  if (send) send.disabled = false;
}

// Reassemble persisted chat events into the turn shape renderTurn expects. The
// transcript is a flat append log; we group by run_id so each assistant turn
// reunites its think / tool_step / text rows the way the live path renders them:
//   - a `user` event is its own turn ({role:'user', text})
//   - assistant `text`/`think`/`tool_step` events sharing a run_id become ONE
//     assistant turn ({role:'assistant', text:<concatenated>, steps:[...]})
//   - an `error` event becomes an error turn ({role:'error', text})
// `result` events are metadata and skipped. Tool verbs map through toolKind so
// replayed steps read identically to live ones (qmd → "Searched", etc.).
function groupEventsIntoTurns(events) {
  const turns = [];
  let current = null; // open assistant turn being assembled
  const flush = () => { if (current) { turns.push(current); current = null; } };
  for (const ev of (events || [])) {
    if (!ev || typeof ev !== 'object') continue;
    const kind = ev.kind;
    if (kind === 'result') continue; // metadata, not rendered
    if (ev.role === 'user' || (kind === 'text' && ev.role === 'user')) {
      flush();
      turns.push({ role: 'user', text: ev.text || '' });
      continue;
    }
    if (kind === 'error') {
      flush();
      turns.push({ role: 'error', text: ev.text || '' });
      continue;
    }
    if (kind === 'notice') {
      flush();
      turns.push({ role: 'notice', text: ev.text || '' });
      continue;
    }
    // assistant think / tool_step / text — assemble into one turn per run_id.
    if (current && current.run_id !== ev.run_id) flush();
    if (!current) current = { role: 'assistant', text: '', steps: [], run_id: ev.run_id };
    if (kind === 'think') {
      current.steps.push({ kind: 'think', label: ev.text || '' });
    } else if (kind === 'tool_step') {
      current.steps.push({ kind: toolKind(ev.verb), label: ev.target || ev.verb || '' });
    } else if (kind === 'text') {
      current.text += ev.text || '';
    }
  }
  flush();
  return turns;
}

// ── Turn rendering (single column — user bubble · assistant prose) ───
function renderTurn(turn, instant) {
  const el = document.createElement('div');
  el.className = `chat-turn turn-${turn.role}${instant ? ' show' : ''}`;
  if (turn.role === 'user') {
    el.innerHTML = `<div class="turn-text">${escapeHtml(turn.text || '')}</div>`;
    return el;
  }
  if (turn.role === 'error') {
    // Mirror the live error path (renderEvent 'error' branch): an assistant-style
    // turn flagged turn-error carrying the plain failure text.
    el.className = `chat-turn turn-assistant turn-error${instant ? ' show' : ''}`;
    const t = document.createElement('div');
    t.className = 'turn-text';
    t.textContent = turn.text || 'The chat run failed. You can retry.';
    el.appendChild(t);
    return el;
  }
  if (turn.role === 'notice') {
    // An advisory aside ("I can't do that from here — use the button"), NOT a
    // failure. Distinct calm styling; the message itself is ours, so rendering
    // it through the XSS-safe markdown renderer is fine (and shows the blocked
    // command in a code span).
    el.className = `chat-turn turn-notice${instant ? ' show' : ''}`;
    el.innerHTML = `<div class="notice-inner">${NOTICE_SVG}<div class="notice-text">${renderMarkdown(turn.text || '')}</div></div>`;
    return el;
  }
  const stepsBox = document.createElement('div');
  stepsBox.className = 'turn-steps';
  renderStepsInto(stepsBox, turn.steps || [], instant);
  el.appendChild(stepsBox);
  const txt = document.createElement('div');
  txt.className = 'turn-text';
  txt.innerHTML = renderMarkdown(turn.text || '');
  el.appendChild(txt);
  return el;
}

function stepHtml(s) {
  const VERB = { read: 'Read', search: 'Searched', write: 'Wrote', run: 'Ran' };
  if (s.kind === 'think') return `<div class="tool-think">${escapeHtml(s.label)}</div>`;
  return `<div class="tool-step"><span class="tool-verb">${VERB[s.kind] || 'Tool'}</span><span class="tool-target">${escapeHtml(s.label)}</span></div>`;
}

// Map a tool verb (e.g. "Read", "grep", "mcp__qmd__query") to a step kind so the
// rendered verb word matches across the live SSE path and history replay. qmd
// MCP tools (query/get/multi_get) are semantic searches → "search". Everything
// else falls back to "run" ("Ran").
function toolKind(verb) {
  const v = (verb || 'tool').toLowerCase();
  if (v.startsWith('mcp__qmd')) return 'search';
  const kindMap = { read: 'read', grep: 'search', glob: 'search', write: 'write', edit: 'write', bash: 'run' };
  return kindMap[v] || 'run';
}

// Collapse threshold — runs with this many tool calls fold into one line.
const STEP_COLLAPSE_AT = 5;
const CHEV_SVG = '<svg class="chev-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4l4 4-4 4"/></svg>';
const CHECK_SVG = '<svg class="step-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-6.5"/></svg>';
// Info glyph for the blocked-tool notice card (rendered in renderTurn at call
// time, so a forward reference from a function body declared earlier is fine).
const NOTICE_SVG = '<svg class="notice-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.5"/><path d="M8 7.5v3.5"/><circle cx="8" cy="5" r="0.6" fill="currentColor" stroke="none"/></svg>';

function elFromHTML(html) { const h = document.createElement('div'); h.innerHTML = html; return h.firstChild; }

// A collapsible group: a hidden toggle header + an animatable list of rows.
function makeStepsGroup() {
  const group = document.createElement('div'); group.className = 'steps-group';
  const toggle = document.createElement('button'); toggle.className = 'steps-toggle'; toggle.type = 'button'; toggle.style.display = 'none';
  const list = document.createElement('div'); list.className = 'steps-list';
  const inner = document.createElement('div'); inner.className = 'steps-inner';
  list.appendChild(inner); group.appendChild(toggle); group.appendChild(list);
  return { group, toggle, list, inner };
}

// Fold the run down to one calm line; click toggles the full list back open.
function collapseGroup(g, count) {
  g.list.classList.add('collapsed');
  g.toggle.style.display = 'inline-flex';
  g.toggle.innerHTML = `${CHECK_SVG}<span class="steps-count">Worked across ${count} steps</span>${CHEV_SVG}`;
  g.toggle.onclick = () => {
    const collapsed = g.list.classList.toggle('collapsed');
    g.toggle.classList.toggle('open', !collapsed);
    scrollThread();
  };
}

// Render steps into a box (instant, for history). Leading reasoning lines stay
// visible; from the first tool call on, rows go into a group that collapses if
// the run is long.
function renderStepsInto(stepsBox, steps, instant) {
  if (!steps || !steps.length) return;
  const firstTool = steps.findIndex(s => s.kind !== 'think');
  const lead = firstTool < 0 ? steps : steps.slice(0, Math.max(firstTool, 0));
  const rest = firstTool < 0 ? [] : steps.slice(firstTool);
  lead.forEach(s => { const r = elFromHTML(stepHtml(s)); if (instant) r.classList.add('in'); stepsBox.appendChild(r); });
  if (!rest.length) return;
  const g = makeStepsGroup();
  rest.forEach(s => { const r = elFromHTML(stepHtml(s)); if (instant) r.classList.add('in'); g.inner.appendChild(r); });
  stepsBox.appendChild(g.group);
  const toolCount = rest.filter(s => s.kind !== 'think').length;
  if (toolCount >= STEP_COLLAPSE_AT) collapseGroup(g, toolCount);
}

function scrollThread() {
  const body = document.getElementById('chat-body');
  if (body) body.scrollTop = body.scrollHeight;
}

// ── Send + real SSE stream ───────────────────────────────────────────
async function sendChat() {
  if (!chatState || chatState.busy) return;
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = ''; input.style.height = 'auto';
  chatState.busy = true;
  document.getElementById('chat-send').disabled = true;
  const empty = document.getElementById('chat-empty'); if (empty) empty.classList.add('gone');
  const thread = document.getElementById('chat-thread');
  const userTurn = renderTurn({ role: 'user', text }, false);
  thread.appendChild(userTurn); revealNow(userTurn, 'show'); scrollThread();

  // assistant turn shell with typing indicator
  const a = document.createElement('div');
  a.className = 'chat-turn turn-assistant show';
  a.innerHTML = `<div class="turn-steps"></div><div class="turn-text"><span class="typing"><span></span><span></span><span></span></span></div>`;
  thread.appendChild(a); scrollThread();
  const stepsBox = a.querySelector('.turn-steps');
  const textBox = a.querySelector('.turn-text');
  let typingCleared = false, liveGroup = null, toolCount = 0, sawText = false, streamDone = false, rawText = '';
  const clearTyping = () => { if (!typingCleared) { textBox.innerHTML = ''; typingCleared = true; } };

  try {
    const resp = await fetch(`${API}/tasks/${chatState.task.id}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    if (resp.status === 409) { clearTyping(); textBox.textContent = 'The agent is currently working on this task. Try again in a moment.'; throw new Error('busy'); }
    if (!resp.ok || !resp.body) { clearTyping(); textBox.textContent = 'Could not reach the assistant. You can retry.'; throw new Error('http'); }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line.
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        handleFrame(frame);
      }
      // The `event: done` sentinel is the authoritative end-of-turn signal —
      // break on it rather than waiting for the TCP stream to close (a
      // keep-alive connection may never deliver reader `done`, which would
      // leave the composer disabled forever).
      if (streamDone) break;
    }
  } catch (e) { /* message already shown on the turn */ }

  function handleFrame(frame) {
    // A frame may carry "event: done" and/or a "data: {...}" line.
    let isDone = false, dataLine = null;
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:') && line.slice(6).trim() === 'done') isDone = true;
      else if (line.startsWith('data:')) dataLine = line.slice(5).trim();
    }
    if (isDone) {
      streamDone = true;   // terminal sentinel - stop the read loop
      // Live turn completed cleanly (socket intact: the backend only emits the
      // `event: done` sentinel from _sse_end, never on client disconnect). Settle
      // the left detail from real persisted state so any artifact edits the agent
      // made this turn appear without reopening the panel. This is the LIVE-only
      // trigger: the GET-history replay path never reaches here, matching base
      // (which settled only on live-turn completion, not on replay).
      settleDetailFromServer();
      return;
    }
    if (!dataLine) return;
    let ev; try { ev = JSON.parse(dataLine); } catch (_) { return; }
    renderEvent(ev);
  }

  function renderEvent(ev) {
    if (ev.kind === 'think') {
      clearTyping();
      const t = elFromHTML(stepHtml({ kind: 'think', label: ev.text || '' }));
      stepsBox.appendChild(t); revealNow(t, 'in'); scrollThread();
    } else if (ev.kind === 'tool_step') {
      clearTyping();
      if (!liveGroup) { liveGroup = makeStepsGroup(); stepsBox.appendChild(liveGroup.group); }
      const row = elFromHTML(stepHtml({ kind: toolKind(ev.verb), label: ev.target || ev.verb || '' }));
      liveGroup.inner.appendChild(row); revealNow(row, 'in'); scrollThread();
      toolCount += 1;
      if (toolCount === STEP_COLLAPSE_AT) collapseGroup(liveGroup, toolCount);
      else if (toolCount > STEP_COLLAPSE_AT) { liveGroup.toggle.querySelector('.steps-count').textContent = `Worked across ${toolCount} steps`; }
    } else if (ev.kind === 'text') {
      clearTyping();
      if (!sawText) { sawText = true; textBox.textContent = ''; }
      // Accumulate the raw streamed markdown and re-render the whole buffer each
      // token so partial markdown resolves correctly once a pair closes.
      rawText += ev.text || '';
      textBox.innerHTML = renderMarkdown(rawText);
      scrollThread();
    } else if (ev.kind === 'error') {
      clearTyping();
      textBox.textContent = ev.text || 'The chat run failed. You can retry.';
      a.classList.add('turn-error');
    } else if (ev.kind === 'notice') {
      // A blocked-tool advisory. The model's own (possibly misleading) prose may
      // already be in `a`; the notice appears as its own calm card right after,
      // matching how history replay renders it (a separate notice turn).
      clearTyping();
      const n = renderTurn({ role: 'notice', text: ev.text || '' }, false);
      thread.appendChild(n); revealNow(n, 'show'); scrollThread();
    }
    // NOTE: turn-completion settle is driven by the `event: done` sentinel (see
    // handleFrame), not a `result` event: the live read tails the durable
    // transcript, which does NOT carry the runner's `result` metadata frame.
  }

  chatState.busy = false;
  document.getElementById('chat-send').disabled = false;
  scrollThread();
}

// ── Settle the LEFT detail from real persisted state ─────────────────
// On a live turn's clean completion (the `event: done` SSE sentinel), re-read
// the current task and refresh the left pane so any artifact edits the agent
// made during the turn appear, plus the
// (blurred) board behind. The chat itself never fires an external write — this
// is read-and-reflect only. Prefer the board's own detail renderer (openTask)
// so the left pane stays a single source of truth; fall back to in-place chip /
// activity updates if openTask isn't available.
function pulse(el) { if (!el) return; el.classList.remove('dt-pulse'); void el.offsetWidth; el.classList.add('dt-pulse'); }

function setStatus(label, cls) {
  const st = document.getElementById('dt-status');
  if (st) { st.className = `dt-status ${cls}`; st.innerHTML = `${svgIcon('complete')}${label}`; pulse(st); }
}
function markFooterDone(label) {
  const right = document.querySelector('#modal-actions .dt-foot-right');
  if (right) right.innerHTML = `<span class="dt-done-note">${svgIcon('complete')}${escapeHtml(label || 'Cleared')}</span>`;
}
function addDetailActivity(content, type) {
  const thread = document.getElementById('dt-thread');
  if (!thread) return;
  const e = document.createElement('div');
  e.className = `dt-event ev-${type} dt-event-new`;
  e.innerHTML = `<span class="dt-ev-dot"></span><div><div class="dt-ev-meta"><span class="dt-ev-actor">Task assistant</span><span>just now</span><span class="dt-ev-kind">${escapeHtml(type.replace(/-/g, ' '))}</span></div><div class="dt-ev-body">${escapeHtml(content)}</div></div>`;
  thread.appendChild(e);
  revealNow(e, 'in');
}

async function settleDetailFromServer() {
  if (!chatState || !chatState.task) return;
  const id = chatState.task.id;
  // Refresh the (blurred) board behind so card state stays current.
  if (typeof fetchTasks === 'function') { try { fetchTasks(); } catch (_) {} }

  // Re-render the left detail in place via the board's own renderer. openTask
  // re-fetches GET /api/tasks/<id> and rebuilds the task pane, so any artifact
  // edits the agent persisted this turn appear. It targets the same modal nodes
  // the split workspace reuses (#modal-body / #modal-actions / #modal-title),
  // and leaves the chat pane untouched.
  if (typeof openTask === 'function') {
    try { await openTask(id, true); return; } catch (_) { /* fall through to lightweight update */ }
  }

  // Fallback: lightweight in-place refresh if the full renderer is unavailable.
  try {
    const res = await fetch(`${API}/tasks/${id}`);
    if (!res.ok) return;
    const task = await res.json();
    chatState.task = task;
    const lastOut = (task.activity_log || []).filter(e => e.type === 'output').pop();
    if (lastOut) addDetailActivity(lastOut.content, 'output');
    const body = document.getElementById('message-body-text');
    if (body && task.message_body) { body.textContent = task.message_body; pulse(body.closest('.dt-msg-bubble')); }
  } catch (_) {}
}
