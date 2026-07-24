// card-registry.js — declarative card rendering driven by /cardtypes/registry.json
//
// Task 5 (Magnolia Phase 3): the renderCard control flow (which signal/action
// builder runs, and in what slot order) is now read from the registry instead of
// being hardcoded in board.js. The HTML-producing helpers (judgeScoreBadge,
// statusMark, outputLink, the chip markup, quickDone…) still live in board.js and
// are CALLED from here unchanged — so the emitted markup is provably equivalent.
//
// Globals consumed (all resolved at call time, defined in core.js / icons.js /
// board.js): escapeHtml, svgIcon, meetingName, obsidianUri, QUEUE_META,
// judgeScoreBadge, statusMark, outputLink, isCronTask, quickDone, fetchTasks.

// ─── Registry load (once, cached, graceful) ─────────────────────────────────
// We start the fetch the moment this script loads. Until it resolves (or if it
// fails), renderCardFromRegistry falls back to the built-in `task` contract:
// every matching signal + the default [mark_done, open_output] actions — which is
// byte-identical to what the registry's `task` cardType prescribes. So the board
// renders correctly even before the registry arrives, and survives a fetch error.

let _cardRegistry = null;       // parsed registry.json, or null until loaded
let _cardRegistryReady = false; // true once a load attempt has settled (ok or failed)

const _cardRegistryPromise = fetch('/cardtypes/registry.json')
  .then(res => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  })
  .then(reg => {
    _cardRegistry = reg;
    _cardRegistryReady = true;
    // If tasks already rendered against the fallback, refresh the Now and Board
    // views now that the registry is authoritative. A no-op today (fallback ≡
    // registry for the only live `task` card type), but correct once non-task
    // types ship. Each call is guarded so it's safe if the fn isn't present yet.
    if (typeof fetchTasks === 'function' && typeof allTasks !== 'undefined' && Array.isArray(allTasks) && allTasks.length) {
      if (typeof renderNow === 'function') renderNow();
      if (typeof renderBoard === 'function') renderBoard();
    }
    return reg;
  })
  .catch(err => {
    // Degrade gracefully — stay on the built-in `task` contract.
    console.warn('[card-registry] registry.json unavailable, using built-in fallback:', err.message);
    _cardRegistryReady = true;
    return null;
  });

function ensureCardRegistry() { return _cardRegistryPromise; }

// ─── Signal predicates ──────────────────────────────────────────────────────
// Keyed by the 8 signal ids in cardtypes/signal-ids.txt. Each (task) => bool
// encodes the EXACT condition that was inline in board.js. The `today` string is
// computed per-call the same way board.js did (new Date().toISOString().slice(0,10)).
const signalPredicates = {
  // Non-waiting due chips only; split overdue vs due on String(date) < today.
  due(task) {
    if (task.queue === 'waiting') return false;
    if (!task.due) return false;
    const today = new Date().toISOString().slice(0, 10);
    return !(String(task.due) < today);
  },
  overdue(task) {
    if (task.queue === 'waiting') return false;
    if (!task.due) return false;
    const today = new Date().toISOString().slice(0, 10);
    return String(task.due) < today;
  },
  // Waiting-queue chips only.
  waiting_on(task) {
    return task.queue === 'waiting' && !!task.waiting_on;
  },
  waiting_due(task) {
    return task.queue === 'waiting' && !!task.waiting_expected;
  },
  schedule(task) {
    return task.task_type === 'schedule-meeting';
  },
  message(task) {
    return task.task_type === 'send-message';
  },
  jira_draft(task) {
    return !!(task.body && task.body.includes('<!-- JIRA_DRAFT -->'));
  },
  cron(task) {
    return isCronTask(task);
  },
};

// ─── Signal markup ──────────────────────────────────────────────────────────
// One builder per signal id, producing the EXACT chip markup board.js emitted.
// Kept byte-identical to the original inline strings so equivalence holds.
const _signalRenderers = {
  due(task) {
    return `<span class="chip chip-due">${svgIcon('due')}due ${task.due}</span>`;
  },
  overdue(task) {
    return `<span class="chip chip-overdue">${svgIcon('overdue')}overdue · ${task.due}</span>`;
  },
  waiting_on(task) {
    return `<span class="chip chip-waiting">${svgIcon('hourglass')}${escapeHtml(task.waiting_on)}</span>`;
  },
  waiting_due(task) {
    const today = new Date().toISOString().slice(0, 10);
    const overdue = String(task.waiting_expected) < today;
    return overdue
      ? `<span class="chip chip-overdue">${svgIcon('overdue')}overdue · ${task.waiting_expected}</span>`
      : `<span class="chip chip-due">${svgIcon('due')}by ${task.waiting_expected}</span>`;
  },
  schedule(task) {
    return `<span class="chip chip-meeting">${svgIcon('meeting')}schedule</span>`;
  },
  message(task) {
    return `<span class="chip chip-meeting" style="color:var(--q-human);background:color-mix(in oklab, var(--q-human) 16%, transparent)">${svgIcon('chat')}message</span>`;
  },
  jira_draft(task) {
    return `<span class="chip chip-cron" style="color:var(--accent);background:var(--accent-soft)">${svgIcon('jira')}jira draft</span>`;
  },
  cron(task) {
    return `<span class="chip chip-cron">${svgIcon('cron')}cron</span>`;
  },
};

// Render order matters for byte-equivalence. board.js emitted, in this order:
// waiting_on, waiting_due (only when waiting), due/overdue (only when not waiting),
// schedule, message, jira_draft, cron. The predicates already gate queue, so we
// just keep this fixed sequence for `signals: "auto"`.
const _autoSignalOrder = ['waiting_on', 'waiting_due', 'due', 'overdue', 'schedule', 'message', 'jira_draft', 'cron'];

function _renderSignals(task, signalsSpec) {
  // signalsSpec: "auto" → every matching signal in canonical order;
  //              array  → only those ids whose predicate also matches.
  const ids = signalsSpec === 'auto'
    ? _autoSignalOrder
    : (Array.isArray(signalsSpec) ? signalsSpec : []);
  const parts = [];
  for (const id of ids) {
    const pred = signalPredicates[id];
    const render = _signalRenderers[id];
    if (pred && render && pred(task)) parts.push(render(task));
  }
  return parts.length ? `<div class="card-signals">${parts.join('')}</div>` : '';
}

// ─── Card-kind metadata (head label + glyph for the new kinds) ───────────────
// The head slot shows a queue glyph+label for plain tasks; for the new kinds it
// reads as the KIND, in plain language, so the card announces what it is.
const KIND_META = {
  recommendation: { label: 'suggestion', icon: 'spark', cls: 'kind-recommendation' },
  receipt:        { label: 'handled',    icon: 'receipt', cls: 'kind-receipt' },
  graduation:     { label: 'trust',      icon: 'ladder', cls: 'kind-graduation' },
  // icon 'jira' is provisional: confirm is a general Tier-2 gate that will serve
  // other families (calendar/doc_sync) later, so this PM-specific glyph is a placeholder.
  confirm:        { label: 'confirm',    icon: 'jira',   cls: 'kind-confirm' },
  'program-setup': { label: 'program setup', icon: 'spark', cls: 'kind-program-setup' },
};
const _TIER_WORD = { shadow: 'observe-only', supervised: 'supervised', autonomous: 'autonomous' };
function _tierWord(t) { return _TIER_WORD[(t || '').toLowerCase()] || (t || 'observe-only'); }
function _gradNote(proposed, type) {
  const ty = type || 'this task-type';
  if ((proposed || '').toLowerCase() === 'supervised')
    return `Supervised means ${ty} runs on its own but waits for your yes before anything leaves. If quality slips, it returns to observe-only automatically.`;
  if ((proposed || '').toLowerCase() === 'autonomous')
    return `Autonomous means ${ty} completes and ships without waiting — you can still review anything. It steps back down if quality drops.`;
  return `If quality later drops, ${ty} steps back down on its own.`;
}

// ─── Body renderers ──────────────────────────────────────────────────────────
// diff / preview / agreement card faces. Faces render from the list PROJECTION
// (no `body`), so these use ONLY projected fields. Full detail lives in the modal.
const bodyRenderers = {
  // Recommendation — what change is being proposed, glanceable.
  diff(task) {
    const hasPatch = !!task.patch_path;
    const file = hasPatch ? escapeHtml(task.patch_path.split('/').pop()) : 'No file — apply by hand';
    return `<div class="card-body card-kind-body rec-body" data-card-body="diff">
      <div class="rec-change">
        <span class="rec-change-icon">${svgIcon(hasPatch ? 'patch' : 'spark')}</span>
        <span class="rec-change-main">
          <span class="rec-change-label">Proposed change${hasPatch ? '' : ' <span class="rec-manual">(manual)</span>'}</span>
          <span class="rec-change-file">${file}</span>
        </span>
      </div>
    </div>`;
  },
  // Receipt — what it did · how well · that it's reversible. Three things, no more.
  preview(task) {
    const did = escapeHtml(task.receipt_summary || String(task.title || '').replace(/^Applied:\s*/i, ''));
    let how = '';
    if (task.judge_score != null) {
      const band = task.judge_score >= 8 ? 'good' : (task.judge_score >= 5 ? 'mid' : 'low');
      how = `<div class="receipt-how"><span class="receipt-score ${band}">${task.judge_score}<span class="card-score-of">/10</span></span>${task.judge_why ? `<span class="receipt-why">${escapeHtml(task.judge_why)}</span>` : ''}</div>`;
    }
    // Honest revert copy. An auto-ship already left the building (email/ticket
    // sent) — "Undo" can't unsend it; what it CAN do is stop this type from
    // auto-shipping again. Branch on receipt_kind (projected onto the list face
    // and the detail object; missing → safe default).
    const revert = task.receipt_kind === 'autoship'
      ? `Already sent — <b>Undo</b> stops auto-shipping this type.`
      : `Applied — <b>Undo</b> reverts this change.`;
    return `<div class="card-body card-kind-body receipt-body" data-card-body="preview">
      <div class="receipt-did">${did}</div>
      ${how}
      <div class="receipt-revert">${svgIcon('undo')}<span>${revert}</span></div>
    </div>`;
  },
  // Program setup — program type + brief prompt; list face has no body, so only
  // projected fields are available (program_type was added to the list projection).
  'program-preview'(task) {
    const ptype = escapeHtml(task.program_type || 'unknown');
    return `<div class="card-body card-kind-body psetup-body" data-card-body="program-preview">
      <div class="psetup-type">
        <span class="psetup-type-label">Program type</span>
        <span class="psetup-type-value">${ptype}</span>
      </div>
      <div class="psetup-hint">Open to add context via chat</div>
    </div>`;
  },
  // Graduation — the evidence, reassuringly: tier move · three numbers · what changes.
  agreement(task) {
    const from = _tierWord(task.grad_current_tier), to = _tierWord(task.grad_proposed_tier);
    const ex = Array.isArray(task.grad_examples) ? task.grad_examples : [];
    const exHtml = ex.length
      ? `<div class="grad-examples"><span class="grad-ex-lbl">Recent runs</span>${ex.map(id => `<a class="grad-ex" onclick="event.stopPropagation();openTask('${id}')">${escapeHtml(id)}</a>`).join('')}</div>`
      : '';
    const num = v => (v == null ? '—' : v + '%');
    return `<div class="card-body card-kind-body grad-body" data-card-body="agreement">
      <div class="grad-ladder">
        <span class="grad-tier is-from">${escapeHtml(from)}</span>
        <span class="grad-arrow">${svgIcon('arrowRight')}</span>
        <span class="grad-tier is-to">${escapeHtml(to)}</span>
      </div>
      <div class="grad-stats">
        <div class="grad-stat"><span class="grad-stat-num">${num(task.grad_approval_pct)}</span><span class="grad-stat-lbl">you approve</span></div>
        <div class="grad-stat"><span class="grad-stat-num">${num(task.grad_agreement_pct)}</span><span class="grad-stat-lbl">judge agrees</span></div>
        <div class="grad-stat"><span class="grad-stat-num">${task.grad_n ?? '—'}</span><span class="grad-stat-lbl">recent runs</span></div>
      </div>
      <div class="grad-note">${escapeHtml(_gradNote(task.grad_proposed_tier, task.grad_task_type))}</div>
      ${exHtml}
    </div>`;
  },
};

function _renderBody(task, bodyName) {
  if (!bodyName) return '';
  const r = bodyRenderers[bodyName];
  return r ? r(task) : '';
}

// ─── Actions ──────────────────────────────────────────────────────────────────
// The `task` cardType uses [mark_done, open_output]. We reproduce board.js's exact
// markup and its gating: Mark done only when not running; Open output only when
// outputLink(task) returns a target. Other action handlers (accept/reject/keep…)
// belong to card types that ship later — render nothing if their handler is absent.
function _renderActions(task, actionIds) {
  const running = task.agent_status === 'running';
  const parts = [];
  for (const id of (actionIds || [])) {
    if (id === 'mark_done') {
      if (!running) parts.push(`<button class="card-action primary" onclick="quickDone('${task.id}', event)">${svgIcon('done')}Mark done</button>`);
    } else if (id === 'open_output') {
      const out = outputLink(task);
      if (out) {
        const ext = out.external ? ' target="_blank" rel="noopener"' : '';
        parts.push(`<a class="card-action" href="${escapeHtml(out.href)}"${ext} onclick="event.stopPropagation()">${svgIcon('output')}${out.label}</a>`);
      }
    }
    // Card-type action verbs → POST /api/tasks/{id}/{verb} via cardAction (tasks.js).
    // The board refresh after the call is the confirmation (toast suppresses success).
    else if (id === 'accept') {
      parts.push(`<button class="card-action primary" onclick="cardAction('${task.id}','accept',event)">${svgIcon('done')}Accept</button>`);
    } else if (id === 'reject') {
      parts.push(`<button class="card-action" onclick="cardAction('${task.id}','reject',event)">Reject</button>`);
    } else if (id === 'graduate') {
      parts.push(`<button class="card-action primary" onclick="cardAction('${task.id}','graduate',event)">${svgIcon('done')}Graduate</button>`);
      // "Not yet" — a quiet dismiss; not wired server-side yet, so it just settles locally.
      parts.push(`<button class="card-action card-action-quiet" onclick="cardDismiss('${task.id}',event)">Not yet</button>`);
    } else if (id === 'keep') {
      parts.push(`<button class="card-action primary" onclick="cardAction('${task.id}','keep',event)">${svgIcon('done')}Keep</button>`);
    } else if (id === 'undo') {
      parts.push(`<button class="card-action" onclick="cardAction('${task.id}','undo',event)">Undo</button>`);
    } else if (id === 'confirm') {
      parts.push(`<button class="card-action primary" onclick="cardAction('${task.id}','confirm',event)">${svgIcon('done')}Confirm</button>`);
    }
  }
  return parts.length ? `<div class="card-actions">${parts.join('')}</div>` : '';
}

// ─── Slot builders (head / title / context) ─────────────────────────────────
// These reproduce board.js's head/title/context markup verbatim.
function _renderHead(task, q) {
  const kind = KIND_META[task.card_type];
  let head = `<div class="card-head">`;
  if (kind) {
    head += `<span class="card-queue card-kind-label ${kind.cls}">${svgIcon(kind.icon)}${kind.label}</span>`;
  } else {
    head += `<span class="card-queue">${svgIcon(q.icon)}${q.label}</span>`;
  }
  head += `<span class="card-head-right">${judgeScoreBadge(task)}${statusMark(task)}<span class="card-id">${task.id}</span></span>`;
  head += `</div>`;
  return head;
}

function _renderTitle(task) {
  const prioClass = `prio-${task.priority || 'low'}`;
  return `<div class="card-title"><span class="prio-dot ${prioClass}" title="${task.priority || 'low'} priority"></span><span>${escapeHtml(task.title)}</span></div>`;
}

function _renderContext(task) {
  const ctxParts = [];
  const mtg = meetingName(task.source_meeting);
  if (mtg) ctxParts.push(`<span class="card-from" title="From: ${escapeHtml(task.source_meeting)}">${svgIcon('meeting')}<span>${escapeHtml(mtg)}</span></span>`);
  if (task.domain) ctxParts.push(`<span class="card-domain">${escapeHtml(task.domain)}</span>`);
  return ctxParts.length ? `<div class="card-context">${ctxParts.join('<span class="sep">·</span>')}</div>` : '';
}

// ─── Main entry ──────────────────────────────────────────────────────────────
// renderCardFromRegistry(task, queue) — looks up task.card_type (default 'task'),
// walks the registry slotOrder, and assembles the same outer <div class="card …">
// shape board.js produced. Falls back to the built-in `task` contract when the
// registry is unavailable or the card type is unknown.
const _FALLBACK_REGISTRY = {
  slotOrder: ['head', 'title', 'context', 'signals', 'body', 'actions'],
  cardTypes: {
    task: { signals: 'auto', actions: ['mark_done', 'open_output'], body: null },
  },
};

function renderCardFromRegistry(task, queueName) {
  const reg = _cardRegistry || _FALLBACK_REGISTRY;
  const typeName = task.card_type || 'task';
  const cardType = (reg.cardTypes && reg.cardTypes[typeName])
    || _FALLBACK_REGISTRY.cardTypes.task;
  const slotOrder = reg.slotOrder || _FALLBACK_REGISTRY.slotOrder;

  const q = QUEUE_META[task.queue] || QUEUE_META[queueName] || QUEUE_META.human;

  const slots = {
    head:    () => _renderHead(task, q),
    title:   () => _renderTitle(task),
    context: () => _renderContext(task),
    signals: () => _renderSignals(task, cardType.signals),
    body:    () => _renderBody(task, cardType.body),
    actions: () => _renderActions(task, cardType.actions),
  };

  let inner = '';
  for (const slot of slotOrder) {
    const build = slots[slot];
    if (build) inner += build();
  }

  const kind = KIND_META[typeName];
  const kindCls = kind ? ` card-kind ${kind.cls}` : '';
  return `<div class="card ${q.cls}${kindCls}" data-task-id="${task.id}" onclick="openTask('${task.id}')">${inner}</div>`;
}
