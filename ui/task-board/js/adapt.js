// ─── Adapt · teach Magnolia a new capability ─────────────────────────────
// The Adapt tab: a build-chat lane on the left (powered by headless Claude Code
// via POST /api/adapt over SSE) and an adaptations rail on the right. Every
// element in the chat maps 1:1 to a primitive the model's stream emits —
// thinking lines, tool steps, streamed prose, plus the AskUserQuestion /
// ExitPlanMode cards. Completed adaptations land in the rail where they can be
// toggled on/off, renamed, edited (resume the build session), or deleted.
//
// SSE reader pattern reused from chat.js: fetch -> body.getReader() -> split on
// \n\n -> handle frames. SSE COMMENT lines (": ping" heartbeats) are ignored.
// Markdown prose is rendered via chat.js's renderMarkdown (XSS-safe). Identity
// is never hardcoded here — the server owns the build harness/system prompt.
//
// Depends on globals: API, escapeHtml, toast (core.js), renderMarkdown (chat.js).

const adaptState = {
  busy: false,         // a turn is streaming
  loaded: false,       // rail loaded once on first open
  phase: 'idle',       // idle · questions · plan · building · tweak · done
  stage: 'idle',       // idle · brainstorm · plan · build · ready  (drives tracker)
  adaptationId: null,  // the current chat session's adaptation row (null = fresh)
  ads: [],             // rail rows: { id, name, state }
  reconnectId: null,   // an id whose live run we should attach to on open
};

// Suggestions / placeholders.
const ADAPT_DEFAULT_PLACEHOLDER = 'Describe a capability to add - an integration, automation, or workflow change…';
const ADAPT_ANOTHER_PLACEHOLDER = 'Describe another adaptation…';
const ADAPT_TWEAK_PLACEHOLDER = 'What should be different?';

// Inline SVGs (single-path, token-colored via currentColor).
const ADAPT_SVG = {
  notice: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.5"/><path d="M8 7.5v3.5"/><circle cx="8" cy="5" r="0.6" fill="currentColor" stroke="none"/></svg>',
  check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-6.5"/></svg>',
  chev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4l4 4-4 4"/></svg>',
  pencil: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.4 2.6a1.4 1.4 0 0 1 2 2L5.6 12.4l-2.7.7.7-2.7z"/><path d="M10.2 3.8l2 2"/></svg>',
  trash: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5h10"/><path d="M5.5 4.5V3.2h5V4.5"/><path d="M4.2 4.5l.6 8.3h6.4l.6-8.3"/><path d="M6.6 6.8v4M9.4 6.8v4"/></svg>',
};

// ── Tab entry ────────────────────────────────────────────────────────
// Called by switchTab('adapt'). Wires the composer + chips + New button once,
// loads the rail, and (best-effort) reconnects to any live build.
let adaptWired = false;
function renderAdapt() {
  if (!adaptWired) { wireAdapt(); adaptWired = true; }
  refreshTracker();
  syncNewBtnDisabled();
  loadAdaptations();
}

function wireAdapt() {
  const input = document.getElementById('adapt-input');
  const send = document.getElementById('adapt-send');
  if (input) {
    input.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 140) + 'px';
      syncSendDisabled();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAdapt(); }
    });
  }
  if (send) send.addEventListener('click', sendAdapt);

  // suggestion chips drop a real first-person prompt into the composer (a
  // workable opening the user finishes), not the chip's short label.
  document.querySelectorAll('#adapt-chips .adapt-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      if (input) {
        input.value = chip.dataset.prompt || chip.textContent;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 140) + 'px';
        input.focus();
        // Drop the cursor at the end so the user types right where the prompt
        // leaves off (after the trailing ": ").
        const end = input.value.length;
        input.setSelectionRange(end, end);
        syncSendDisabled();
      }
    });
  });

  // + New adaptation: clear to a clean slate (next send starts a fresh row).
  const newBtn = document.getElementById('adapt-new-btn');
  if (newBtn) newBtn.addEventListener('click', startNewAdaptation);

  // delete-warning modal buttons
  const delCancel = document.getElementById('adapt-del-cancel');
  const delOk = document.getElementById('adapt-del-ok');
  if (delCancel) delCancel.addEventListener('click', closeDeleteModal);
  if (delOk) delOk.addEventListener('click', confirmDelete);
  const delOverlay = document.getElementById('adapt-del-overlay');
  if (delOverlay) delOverlay.addEventListener('click', (e) => { if (e.target === e.currentTarget) closeDeleteModal(); });
}

function syncSendDisabled() {
  const input = document.getElementById('adapt-input');
  const send = document.getElementById('adapt-send');
  if (!input || !send) return;
  send.disabled = adaptState.busy || !input.value.trim();
}

// The + New adaptation button only does something when there's a session to
// clear. On a fresh, empty chat (no session, idle, empty thread) clicking it is
// a no-op that feels broken, so disable it there; also disable it mid-build.
function syncNewBtnDisabled() {
  const btn = document.getElementById('adapt-new-btn');
  if (!btn) return;
  const thread = document.getElementById('adapt-thread');
  const hasThread = !!(thread && thread.children.length);
  const fresh = adaptState.adaptationId === null && adaptState.phase === 'idle' && !hasThread;
  btn.disabled = adaptState.busy || fresh;
  btn.title = btn.disabled
    ? 'Already a fresh slate - just describe what to build'
    : 'Start a fresh adaptation';
}

function adaptSetBusy(busy) {
  adaptState.busy = busy;
  syncSendDisabled();
  syncNewBtnDisabled();
}

function startNewAdaptation() {
  if (adaptState.busy) { toast('A build is still running — let it finish first.', 'warn'); return; }
  adaptState.adaptationId = null;
  adaptState.phase = 'idle';
  adaptState.stage = 'idle';
  const thread = document.getElementById('adapt-thread');
  if (thread) thread.innerHTML = '';
  const empty = document.getElementById('adapt-empty');
  if (empty) empty.classList.remove('gone');
  setComposerPlaceholder(ADAPT_DEFAULT_PLACEHOLDER);
  refreshTracker();
  const input = document.getElementById('adapt-input');
  if (input) { input.value = ''; input.style.height = 'auto'; input.focus(); }
  syncSendDisabled();
  syncNewBtnDisabled();
}

function setComposerPlaceholder(text) {
  const input = document.getElementById('adapt-input');
  if (input) input.placeholder = text;
}

// ── Phase tracker ──────────────────────────────────────────────────────
// Four skewed segments (Brainstorm/Plan/Build/Ready) + one uppercase word.
const ADAPT_STAGES = ['brainstorm', 'plan', 'build', 'ready'];
const ADAPT_STAGE_WORD = { idle: '', brainstorm: 'Brainstorm', plan: 'Plan', build: 'Build', ready: 'Ready' };

function refreshTracker() {
  const stage = adaptState.stage;
  const idx = stage === 'idle' ? -1 : ADAPT_STAGES.indexOf(stage);
  const allDone = stage === 'ready';
  document.querySelectorAll('#adapt-tracker .adapt-seg').forEach((seg, i) => {
    seg.classList.remove('filled', 'active');
    if (allDone || i < idx) seg.classList.add('filled');
    else if (i === idx) seg.classList.add('active');
  });
  const word = document.getElementById('adapt-tracker-word');
  if (word) word.textContent = ADAPT_STAGE_WORD[stage] || '';
}

function setStage(stage) { adaptState.stage = stage; refreshTracker(); }

// ── Thread helpers ──────────────────────────────────────────────────────
function adaptThread() { return document.getElementById('adapt-thread'); }
function adaptScroll() { const b = document.getElementById('adapt-body'); if (b) b.scrollTop = b.scrollHeight; }
function adaptRevealNow(el, cls) { void el.offsetWidth; el.classList.add(cls); }
function adaptHideEmpty() { const e = document.getElementById('adapt-empty'); if (e) e.classList.add('gone'); }

function elFrom(html) { const h = document.createElement('div'); h.innerHTML = html.trim(); return h.firstChild; }

function postUserBubble(text) {
  adaptHideEmpty();
  const el = document.createElement('div');
  el.className = 'adapt-turn turn-user';
  el.innerHTML = `<div class="adapt-bubble"></div>`;
  el.querySelector('.adapt-bubble').textContent = text;
  adaptThread().appendChild(el);
  adaptRevealNow(el, 'show'); adaptScroll();
  return el;
}

function postSessionDivider(label) {
  adaptHideEmpty();
  const el = document.createElement('div');
  el.className = 'adapt-turn turn-session';
  el.innerHTML = `<span class="adapt-session-rule"></span>` +
    `<span class="adapt-session-label"><span class="adapt-session-leaf"></span><span class="adapt-session-text"></span></span>` +
    `<span class="adapt-session-rule"></span>`;
  el.querySelector('.adapt-session-text').textContent = label;
  adaptThread().appendChild(el); adaptScroll();
}

function postNotice(text, terminal) {
  adaptHideEmpty();
  const el = document.createElement('div');
  el.className = 'adapt-turn ' + (terminal ? 'turn-error' : 'turn-notice');
  el.innerHTML = `<div class="adapt-notice">${ADAPT_SVG.notice}<div class="adapt-notice-text"></div></div>`;
  // Our own copy -> markdown is safe; renderMarkdown escapes first.
  el.querySelector('.adapt-notice-text').innerHTML =
    (typeof renderMarkdown === 'function') ? renderMarkdown(text) : escapeHtml(text);
  adaptThread().appendChild(el); adaptRevealNow(el, 'show'); adaptScroll();
}

// ── Work log (thinking + tool steps, collapsed by default) ──────────────
// A "work group" folds the model's reasoning (think rows) AND its tool steps
// into one disclosure that is COLLAPSED by default - the stream stays clean and
// the user expands it only if curious. The flow interleaves these groups with
// prose blocks in arrival order (see openAssistantTurn), so a tool call between
// two sentences breaks them into separate paragraphs instead of gluing them.
const ADAPT_VERB = { Read: 'Read', Grep: 'Searched', Glob: 'Searched', Write: 'Wrote', Edit: 'Wrote', Bash: 'Ran' };
function adaptVerb(raw) {
  const v = String(raw || '').trim();
  if (v.startsWith('mcp__qmd')) return 'Searched';
  return ADAPT_VERB[v] || (v ? 'Ran' : 'Tool');
}

function makeWorkGroup() {
  const group = document.createElement('div'); group.className = 'adapt-steps-group';
  const toggle = document.createElement('button'); toggle.type = 'button';
  toggle.className = 'adapt-steps-toggle'; toggle.style.display = 'inline-flex';
  const list = document.createElement('div'); list.className = 'adapt-steps-list collapsed';
  group.appendChild(toggle); group.appendChild(list);
  const g = { group, toggle, list, tools: 0, thinks: 0 };
  toggle.onclick = () => {
    const collapsed = list.classList.toggle('collapsed');
    toggle.classList.toggle('open', !collapsed);
    adaptScroll();
  };
  setWorkHeader(g);
  return g;
}
// Header label tracks what the group holds: tool work reads "Worked across N
// steps"; a think-only group (reasoning, no tool calls) reads "Thought it
// through". The chevron expands the collapsed body.
function setWorkHeader(g) {
  const label = g.tools > 0
    ? `Worked across ${g.tools} step${g.tools === 1 ? '' : 's'}`
    : 'Thought it through';
  g.toggle.innerHTML =
    `<span class="adapt-step-check">${ADAPT_SVG.check}</span>` +
    `<span class="adapt-steps-count"></span>` +
    `<span class="adapt-step-chev">${ADAPT_SVG.chev}</span>`;
  g.toggle.querySelector('.adapt-steps-count').textContent = label;
}
function thinkRow(text) {
  const r = document.createElement('div'); r.className = 'adapt-think'; r.textContent = text; return r;
}
function toolRow(verb, target) {
  const r = document.createElement('div'); r.className = 'adapt-tool';
  r.innerHTML = `<span class="adapt-verb"></span><span class="adapt-target"></span><span class="adapt-chevtgt">&rsaquo;</span>`;
  r.querySelector('.adapt-verb').textContent = adaptVerb(verb);
  r.querySelector('.adapt-target').textContent = target || '';
  return r;
}

// ── Rail (GET /api/adaptations) ─────────────────────────────────────────
async function loadAdaptations() {
  try {
    const res = await fetch(`${API}/adaptations`);
    if (res.ok) {
      const data = await res.json();
      adaptState.ads = (data && data.adaptations) || [];
    }
  } catch (_) { /* leave whatever we had */ }
  renderRail();
  // Best-effort reconnect: only re-attach to a live build for the session this
  // chat is already bound to (the row we started or resumed). We must NOT
  // hijack a fresh, empty chat by auto-attaching to some unrelated building row
  // — that would clear the empty state the user hasn't acted on yet. A building
  // row owned by a different/abandoned session stays visible in the rail; its
  // pencil resumes it explicitly.
  if (!adaptState.loaded && !adaptState.busy && adaptState.adaptationId) {
    const mine = adaptState.ads.find(a => a.id === adaptState.adaptationId && a.state === 'building');
    if (mine) reconnectAdapt(mine.id);
  }
  adaptState.loaded = true;
}

function railRowVM(ad) {
  const state = ad.state || 'off';
  return { id: ad.id, name: ad.name || ad.id, state, building: state === 'building' };
}

function renderRail() {
  const list = document.getElementById('adapt-rail-list');
  if (!list) return;
  list.innerHTML = '';
  if (!adaptState.ads.length) {
    const empty = document.createElement('div');
    empty.className = 'adapt-rail-empty';
    empty.textContent = 'Nothing here yet - what you build shows up below.';
    list.appendChild(empty);
    return;
  }
  adaptState.ads.forEach(ad => list.appendChild(renderRailRow(railRowVM(ad))));
}

function renderRailRow(vm) {
  const row = document.createElement('div');
  row.className = 'adapt-row' + (vm.state === 'off' ? ' is-off' : '') + (vm.building ? ' is-building' : '');
  row.dataset.id = vm.id;
  row.title = vm.name;

  const dot = document.createElement('span'); dot.className = 'adapt-dot';
  const name = document.createElement('span'); name.className = 'adapt-name'; name.textContent = vm.name;
  row.appendChild(dot); row.appendChild(name);

  if (vm.building) {
    const label = document.createElement('span'); label.className = 'adapt-building-label'; label.textContent = 'Building';
    row.appendChild(label);
    return row; // hidden affordances while building
  }

  // double-click name -> inline rename
  name.addEventListener('dblclick', () => startRename(row, vm));

  const pencil = document.createElement('button');
  pencil.type = 'button'; pencil.className = 'adapt-icon-btn'; pencil.title = 'Edit'; pencil.setAttribute('aria-label', 'Edit this adaptation');
  pencil.innerHTML = ADAPT_SVG.pencil;
  pencil.addEventListener('click', () => resumeAdaptation(vm.id, vm.name));

  const trash = document.createElement('button');
  trash.type = 'button'; trash.className = 'adapt-icon-btn danger'; trash.title = 'Delete'; trash.setAttribute('aria-label', 'Delete this adaptation');
  trash.innerHTML = ADAPT_SVG.trash;
  trash.addEventListener('click', () => openDeleteModal(vm.id, vm.name));

  const toggle = document.createElement('button');
  toggle.type = 'button'; toggle.className = 'adapt-toggle' + (vm.state === 'on' ? ' on' : '');
  toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-checked', String(vm.state === 'on'));
  toggle.title = vm.state === 'on' ? 'On — click to turn off' : 'Off — click to turn on';
  toggle.innerHTML = `<span class="adapt-toggle-knob"></span>`;
  toggle.addEventListener('click', () => toggleAdaptation(vm.id));

  row.appendChild(pencil); row.appendChild(trash); row.appendChild(toggle);
  return row;
}

// UPSERT a rail row from an `adaptation` stream event.
function upsertRail(ev) {
  const id = ev.adaptation_id;
  if (!id) return;
  const existing = adaptState.ads.find(a => a.id === id);
  if (existing) {
    existing.state = ev.state || existing.state;
    if (ev.name) existing.name = ev.name;
  } else {
    adaptState.ads.push({ id, name: ev.name || id, state: ev.state || 'building' });
  }
  renderRail();
}

// ── Toggle (optimistic; revert on failure) ──────────────────────────────
async function toggleAdaptation(id) {
  const ad = adaptState.ads.find(a => a.id === id);
  if (!ad) return;
  const prev = ad.state;
  const next = ad.state === 'on' ? 'off' : 'on';
  ad.state = next; renderRail();
  try {
    const res = await fetch(`${API}/adaptations/${encodeURIComponent(id)}/toggle`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: next }),
    });
    if (!res.ok) throw new Error('toggle failed');
  } catch (_) {
    ad.state = prev; renderRail();
    toast('Could not change that adaptation. Try again.', 'error');
  }
}

// ── Inline rename (double-click name) ────────────────────────────────────
function startRename(row, vm) {
  const name = row.querySelector('.adapt-name');
  if (!name) return;
  const input = document.createElement('input');
  input.className = 'adapt-name-input'; input.value = vm.name;
  name.replaceWith(input); input.focus(); input.select();

  let done = false;
  const commit = async () => {
    if (done) return; done = true;
    const newName = input.value.trim();
    if (!newName || newName === vm.name) { renderRail(); return; }
    try {
      const res = await fetch(`${API}/adaptations/${encodeURIComponent(vm.id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      if (!res.ok) throw new Error('rename failed');
      const ad = adaptState.ads.find(a => a.id === vm.id);
      if (ad) ad.name = newName;
    } catch (_) {
      toast('Could not rename that adaptation.', 'error');
    }
    renderRail();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') { done = true; renderRail(); }
  });
  input.addEventListener('blur', commit);
}

// ── Delete (warning modal) ───────────────────────────────────────────────
let adaptPendingDelete = null;
function openDeleteModal(id, name) {
  adaptPendingDelete = { id, name };
  const t = document.getElementById('adapt-del-title');
  if (t) t.textContent = `Delete "${name}"?`;
  const overlay = document.getElementById('adapt-del-overlay');
  if (overlay) overlay.classList.add('active');
}
function closeDeleteModal() {
  adaptPendingDelete = null;
  const overlay = document.getElementById('adapt-del-overlay');
  if (overlay) overlay.classList.remove('active');
}
async function confirmDelete() {
  if (!adaptPendingDelete) return;
  const { id } = adaptPendingDelete;
  try {
    const res = await fetch(`${API}/adaptations/${encodeURIComponent(id)}/delete`, { method: 'POST' });
    if (!res.ok) throw new Error('delete failed');
    const data = await res.json().catch(() => ({}));
    adaptState.ads = adaptState.ads.filter(a => a.id !== id);
    renderRail();
    if (data && data.partial) toast('Removed, but some changes could not be reverted automatically.', 'warn');
    else toast('Adaptation removed.', 'success');
  } catch (_) {
    toast('Could not delete that adaptation.', 'error');
  }
  closeDeleteModal();
}

// Escape closes the delete modal too.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && adaptPendingDelete) closeDeleteModal();
});

// ── Send a turn (POST /api/adapt, SSE) ───────────────────────────────────
async function sendAdapt() {
  if (adaptState.busy) return;
  const input = document.getElementById('adapt-input');
  const text = (input ? input.value : '').trim();
  if (!text) return;
  input.value = ''; input.style.height = 'auto';
  adaptSetBusy(true);

  postUserBubble(text);
  if (adaptState.stage === 'idle') setStage('brainstorm');
  else if (adaptState.stage === 'plan') setStage('build');

  const body = { message: text };
  if (adaptState.adaptationId) body.adaptation_id = adaptState.adaptationId;

  let resp;
  try {
    resp = await fetch(`${API}/adapt`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (_) {
    postNotice('Could not reach the build assistant. You can retry.', true);
    adaptSetBusy(false); return;
  }

  // A live run is already in progress for this id — reconnect via GET stream.
  if (resp.status === 409 && adaptState.adaptationId) {
    adaptSetBusy(false);
    reconnectAdapt(adaptState.adaptationId);
    return;
  }
  if (!resp.ok || !resp.body) {
    postNotice('The build assistant could not start. You can retry.', true);
    adaptSetBusy(false); return;
  }

  await consumeAdaptStream(resp.body);
  adaptSetBusy(false);
}

// Reconnect to a live (or finished) run: replay the event log then tail.
async function reconnectAdapt(id) {
  if (adaptState.busy) return;
  adaptState.adaptationId = id;
  adaptSetBusy(true);
  let resp;
  try {
    resp = await fetch(`${API}/adapt/stream?adaptation=${encodeURIComponent(id)}`);
  } catch (_) { adaptSetBusy(false); return; }
  if (!resp.ok || !resp.body) { adaptSetBusy(false); return; }
  await consumeAdaptStream(resp.body);
  adaptSetBusy(false);
}

// Shared SSE consumer: opens a fresh assistant turn shell and feeds frames into
// it. Mirrors chat.js's reader (split on \n\n, ignore ": ping" comments, break
// on event: done).
async function consumeAdaptStream(stream) {
  adaptHideEmpty();
  const turn = openAssistantTurn();
  let buf = '', streamDone = false;
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (handleAdaptFrame(frame, turn)) { streamDone = true; }
      }
      if (streamDone) break;
    }
  } catch (_) { /* turn already shows what it has */ }
  finalizeAssistantTurn(turn);
}

// Returns true when the frame is the terminal `event: done` sentinel.
function handleAdaptFrame(frame, turn) {
  let isDone = false, dataLine = null;
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue; // SSE comment (heartbeat) — ignore
    if (line.startsWith('event:') && line.slice(6).trim() === 'done') isDone = true;
    else if (line.startsWith('data:')) dataLine = line.slice(5).trim();
  }
  if (isDone) return true;
  if (!dataLine) return false;
  let ev; try { ev = JSON.parse(dataLine); } catch (_) { return false; }
  renderAdaptEvent(ev, turn);
  return false;
}

// An assistant turn shell: a typing indicator plus a single ordered `flow`
// container. Prose blocks and collapsed work groups are appended to `flow` in
// ARRIVAL order, so the conversation reads top-to-bottom as it actually
// happened. The turn object carries the live render cursors: `tail` records
// what kind of block is currently open ('text' | 'work'); a kind switch starts
// a fresh block (so text after a tool call is its own paragraph, not glued on).
function openAssistantTurn() {
  const el = document.createElement('div');
  el.className = 'adapt-turn turn-assistant show';
  el.innerHTML =
    `<div class="adapt-typing"><span></span><span></span><span></span></div>` +
    `<div class="adapt-flow"></div>`;
  adaptThread().appendChild(el); adaptScroll();
  return {
    el,
    typing: el.querySelector('.adapt-typing'),
    flow: el.querySelector('.adapt-flow'),
    tail: null,                 // 'text' | 'work' — the currently-open block
    textEl: null,               // current prose block element
    textTarget: '', textShown: 0, // full text vs. how much has been revealed
    pumpRAF: null,              // rAF handle for the typewriter reveal
    group: null,                // current work group {group,toggle,list,tools,thinks}
    sawText: false, anyContent: false, typingCleared: false,
    cardHost: el, cardAdded: false, // ask/plan cards append after the flow
  };
}

// ── Typewriter reveal ───────────────────────────────────────────────────
// claude -p delivers prose as whole chunks (often the entire reply at once), so
// without this a "wall of text" just pops in. We reveal the current block's
// text progressively. Rate has a floor (so a one-liner still types) and scales
// with backlog so any amount catches up within ~MAX_SEC — short replies feel
// hand-typed, long ones (or a reconnect replay) snap in quickly, never janky.
const ADAPT_STREAM_CPS = 480;     // floor reveal rate (chars/sec)
const ADAPT_STREAM_MAX_SEC = 1.8; // cap on time to drain the backlog

function renderTextBlock(turn) {
  if (!turn.textEl) return;
  const shown = turn.textTarget.slice(0, turn.textShown);
  turn.textEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(shown) : escapeHtml(shown);
  adaptScroll();
}
function pumpText(turn) {
  if (turn.pumpRAF) return; // already pumping
  let last = (typeof performance !== 'undefined' ? performance.now() : Date.now());
  const step = (now) => {
    turn.pumpRAF = null;
    const dt = Math.max(0, (now - last) / 1000); last = now;
    const backlog = turn.textTarget.length - turn.textShown;
    const rate = Math.max(ADAPT_STREAM_CPS, backlog / ADAPT_STREAM_MAX_SEC);
    turn.textShown = Math.min(turn.textTarget.length, turn.textShown + Math.max(1, Math.ceil(rate * dt)));
    renderTextBlock(turn);
    if (turn.textShown < turn.textTarget.length) turn.pumpRAF = requestAnimationFrame(step);
  };
  turn.pumpRAF = requestAnimationFrame(step);
}
// Reveal the rest of the current prose block immediately (when leaving it for a
// work group, or when the turn ends — no half-typed paragraphs left behind).
function flushTextBlock(turn) {
  if (turn.pumpRAF) { cancelAnimationFrame(turn.pumpRAF); turn.pumpRAF = null; }
  turn.textShown = turn.textTarget.length;
  renderTextBlock(turn);
}

// Open (or reuse) the current prose block. A new block starts whenever the tail
// is not already prose — i.e. after a work group — so consecutive text deltas
// concatenate, but text on the far side of a tool call is a separate paragraph.
function ensureTextBlock(turn) {
  if (turn.tail === 'text' && turn.textEl) return turn.textEl;
  const t = document.createElement('div'); t.className = 'adapt-text';
  turn.flow.appendChild(t);
  turn.textEl = t; turn.textTarget = ''; turn.textShown = 0; turn.tail = 'text';
  return t;
}
// Open (or reuse) the current work group. A new group starts whenever the tail
// is not already a work group — i.e. after prose; finish revealing that prose
// first so a tool step never interrupts a half-typed paragraph.
function ensureWorkGroup(turn) {
  if (turn.tail === 'work' && turn.group) return turn.group;
  if (turn.tail === 'text') flushTextBlock(turn);
  const g = makeWorkGroup();
  turn.flow.appendChild(g.group);
  turn.group = g; turn.tail = 'work';
  return g;
}

function clearTyping(turn) {
  if (turn.typingCleared) return;
  if (turn.typing) turn.typing.remove();
  turn.typingCleared = true;
}
function finalizeAssistantTurn(turn) {
  clearTyping(turn);
  if (turn.tail === 'text') flushTextBlock(turn); // no half-typed paragraph at end
  if (!turn.anyContent && !turn.cardAdded) {
    // Empty assistant turn (e.g. reconnect to a run with no replayable prose):
    // remove the bare shell rather than leaving an empty bubble.
    if (turn.el && turn.el.parentNode) turn.el.parentNode.removeChild(turn.el);
  }
  adaptScroll();
}

function renderAdaptEvent(ev, turn) {
  const kind = ev.kind;
  if (kind === 'think') {
    clearTyping(turn);
    if (!(ev.text || '').trim()) return;
    const g = ensureWorkGroup(turn);
    const r = thinkRow(ev.text);
    g.list.appendChild(r); adaptRevealNow(r, 'in');
    g.thinks += 1; turn.anyContent = true; setWorkHeader(g); adaptScroll();
  } else if (kind === 'tool_step') {
    clearTyping(turn);
    const g = ensureWorkGroup(turn);
    const row = toolRow(ev.verb, ev.target);
    g.list.appendChild(row); adaptRevealNow(row, 'in');
    g.tools += 1; turn.anyContent = true; setWorkHeader(g); adaptScroll();
  } else if (kind === 'text') {
    clearTyping(turn);
    if (!(ev.text || '')) return;
    turn.sawText = true; turn.anyContent = true;
    ensureTextBlock(turn);
    turn.textTarget += ev.text || '';
    pumpText(turn); // reveal progressively (typewriter), not all at once
  } else if (kind === 'notice') {
    clearTyping(turn);
    postNotice(ev.text || 'Heads up.', false);
  } else if (kind === 'error') {
    clearTyping(turn);
    postNotice(ev.text || 'The build run failed. You can retry.', true);
  } else if (kind === 'ask') {
    clearTyping(turn);
    renderAskCard(ev, turn);
    if (adaptState.stage === 'idle' || adaptState.stage === 'brainstorm') setStage('brainstorm');
    adaptState.phase = 'questions';
  } else if (kind === 'plan') {
    clearTyping(turn);
    renderPlanCard(ev, turn);
    setStage('plan');
    adaptState.phase = 'plan';
  } else if (kind === 'adaptation') {
    // A row becomes visible only when a build lands (state 'off'). Upsert the
    // rail; do NOT jump the tracker to Build on a 'building' event (that fired
    // prematurely on keying rows - it should rarely arrive now). The 'off'
    // event is the real Done->Ready moment.
    upsertRail(ev);
    if (ev.adaptation_id && !adaptState.adaptationId) adaptState.adaptationId = ev.adaptation_id;
    if (ev.state === 'building') { adaptState.phase = 'building'; }
    else if (ev.state === 'off') { setStage('ready'); adaptState.phase = 'done'; setComposerPlaceholder(ADAPT_ANOTHER_PLACEHOLDER); }
  } else if (kind === 'result') {
    // metadata; turn ends on event: done. Refresh rail to reflect final state.
    loadAdaptations();
  }
}

// ── "Magnolia asks" (AskUserQuestion) card ───────────────────────────────
// ev shape: { kind:'ask', questions:[{ question/header/prompt, options:[{label,description}] }] }
function renderAskCard(ev, turn) {
  const questions = normalizeAskQuestions(ev);
  if (!questions.length) return;
  turn.cardAdded = true;

  const card = document.createElement('div');
  card.className = 'adapt-card adapt-ask';
  card.innerHTML = `<div class="adapt-ask-head"><span class="adapt-ask-leaf"></span><span class="adapt-ask-kicker">Magnolia asks</span></div>`;

  const sel = questions.map(() => -1); // selected option index per question

  questions.forEach((q, qi) => {
    const qEl = document.createElement('div'); qEl.className = 'adapt-ask-q';
    const prompt = document.createElement('div'); prompt.className = 'adapt-ask-prompt'; prompt.textContent = q.prompt;
    const opts = document.createElement('div'); opts.className = 'adapt-ask-opts';
    q.options.forEach((opt, oi) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'adapt-opt';
      b.innerHTML =
        `<span class="adapt-opt-radio"><span class="adapt-opt-dot"></span></span>` +
        `<span class="adapt-opt-body"><span class="adapt-opt-label"></span><span class="adapt-opt-desc"></span></span>`;
      b.querySelector('.adapt-opt-label').textContent = opt.label;
      b.querySelector('.adapt-opt-desc').textContent = opt.description || '';
      b.addEventListener('click', () => {
        sel[qi] = oi;
        opts.querySelectorAll('.adapt-opt').forEach((x, k) => x.classList.toggle('selected', k === oi));
        confirm.disabled = sel.some(s => s < 0);
      });
      opts.appendChild(b);
    });
    qEl.appendChild(prompt); qEl.appendChild(opts);
    card.appendChild(qEl);
  });

  const foot = document.createElement('div'); foot.className = 'adapt-ask-foot';
  const confirm = document.createElement('button');
  confirm.type = 'button'; confirm.className = 'adapt-primary-btn'; confirm.textContent = 'Use these'; confirm.disabled = true;
  confirm.addEventListener('click', () => {
    if (sel.some(s => s < 0)) return;
    const summaryParts = questions.map((q, qi) => `${q.prompt} ${q.options[sel[qi]].label}`);
    // mark answered (collapse to a check + summary)
    card.innerHTML = `<div class="adapt-ask-head"><span class="adapt-ask-leaf"></span><span class="adapt-ask-kicker">Magnolia asks</span></div>` +
      `<div class="adapt-ask-answered">${ADAPT_SVG.check}<span></span></div>`;
    card.querySelector('.adapt-ask-answered span').textContent = summaryParts.join(' · ');
    const msg = 'Here are my answers:\n' + questions.map((q, qi) => `- ${q.prompt} ${q.options[sel[qi]].label}`).join('\n');
    sendResumeTurn(msg);
  });
  foot.appendChild(confirm);
  card.appendChild(foot);

  turn.cardHost.appendChild(card); adaptScroll();
}

// Tolerant of however the model/normalize shapes questions: pull a prompt and a
// list of {label, description} per question.
function normalizeAskQuestions(ev) {
  const raw = ev.questions || ev.items || [];
  if (!Array.isArray(raw)) return [];
  return raw.map(q => {
    const prompt = q.prompt || q.question || q.header || q.title || '';
    let options = q.options || q.choices || [];
    if (!Array.isArray(options)) options = [];
    options = options.map(o => {
      if (typeof o === 'string') return { label: o, description: '' };
      return { label: o.label || o.text || o.value || '', description: o.description || o.desc || '' };
    }).filter(o => o.label);
    return { prompt, options };
  }).filter(q => q.options.length);
}

// ── "The build" (ExitPlanMode) card ──────────────────────────────────────
function renderPlanCard(ev, turn) {
  const planBody = ev.body || ev.plan || '';
  turn.cardAdded = true;
  const card = document.createElement('div');
  card.className = 'adapt-card';
  card.innerHTML =
    `<div class="adapt-plan-head"><span class="adapt-plan-title">The build</span></div>` +
    `<div class="adapt-plan-body"></div>` +
    `<div class="adapt-plan-foot"></div>`;
  const bodyEl = card.querySelector('.adapt-plan-body');
  bodyEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(planBody) : escapeHtml(planBody);
  const foot = card.querySelector('.adapt-plan-foot');

  const approve = document.createElement('button');
  approve.type = 'button'; approve.className = 'adapt-primary-btn'; approve.textContent = 'Approve & build';
  const adjust = document.createElement('button');
  adjust.type = 'button'; adjust.className = 'adapt-ghost-btn'; adjust.textContent = 'Adjust something';

  approve.addEventListener('click', () => {
    foot.innerHTML = `<span class="adapt-plan-approved">${ADAPT_SVG.check}Approved</span>`;
    sendResumeTurn('Approved — go ahead and build it.');
  });
  adjust.addEventListener('click', () => {
    adaptState.phase = 'tweak';
    setComposerPlaceholder(ADAPT_TWEAK_PLACEHOLDER);
    postNotice("Tell me below what you'd like different. Nothing builds until you approve.", false);
    const input = document.getElementById('adapt-input');
    if (input) input.focus();
  });

  foot.appendChild(approve); foot.appendChild(adjust);
  turn.cardHost.appendChild(card); adaptScroll();
}

// Send an answer/approval as a resume turn (carries the current adaptation_id).
async function sendResumeTurn(message) {
  if (adaptState.busy) return;
  adaptSetBusy(true);
  const body = { message };
  if (adaptState.adaptationId) body.adaptation_id = adaptState.adaptationId;
  let resp;
  try {
    resp = await fetch(`${API}/adapt`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (_) { postNotice('Could not reach the build assistant. You can retry.', true); adaptSetBusy(false); return; }
  if (resp.status === 409 && adaptState.adaptationId) { adaptSetBusy(false); reconnectAdapt(adaptState.adaptationId); return; }
  if (!resp.ok || !resp.body) { postNotice('The build assistant could not continue. You can retry.', true); adaptSetBusy(false); return; }
  await consumeAdaptStream(resp.body);
  adaptSetBusy(false);
}

// ── Resume/edit (pencil) ─────────────────────────────────────────────────
function resumeAdaptation(id, name) {
  if (adaptState.busy) { toast('A build is still running — let it finish first.', 'warn'); return; }
  adaptState.adaptationId = id;
  adaptHideEmpty();
  postSessionDivider(`Resumed ${name} session`);
  setComposerPlaceholder(ADAPT_TWEAK_PLACEHOLDER);
  adaptState.phase = 'tweak';
  const input = document.getElementById('adapt-input');
  if (input) input.focus();
  // If a build is live for this row, attach to its stream now.
  const ad = adaptState.ads.find(a => a.id === id);
  if (ad && ad.state === 'building') reconnectAdapt(id);
}
