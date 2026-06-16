// ─── Cadence Tab ────────────────────────────────────────────────────
// Read-only "standing loops" view: the recurring, long-running work the TPM
// agent runs in the background, grouped by family, each program one row whose
// column-2 treatment branches on its model (pipeline | target | cycle |
// register). Mirrors js/schedules.js: fetch → build HTML string → innerHTML.
//
// render_view (GET /api/cadence) returns DATA ONLY — this module owns ALL
// color/tone, derived client-side from drift / status / age. Tokens only;
// the view inherits every Mood for free.

let cadenceData = null;
const cadenceExpanded = new Set();

// ─── Derived tone (the single most important rule) ──────────────────
// drift → tone token. Drives the health word, the current pipeline dot, the
// metric/series color, and the current-phase meta.
function cadenceTone(drift) {
  if (drift === 'broken') return 'var(--danger)';
  if (drift === 'drifting') return 'var(--warning)';
  if (drift === 'blind') return 'var(--text-dim)';
  return 'var(--text-muted)'; // holding
}

// drift → health word (column 3). A slice simplification: the word is derived
// from drift (color carries the meaning, not the word).
function cadenceHealthWord(drift) {
  if (drift === 'broken') return 'At risk';
  if (drift === 'drifting') return 'Slipping';
  if (drift === 'blind') return 'Needs grounding';
  return 'On track'; // holding
}

function cadenceSoft(color, pct) {
  return `color-mix(in oklab, ${color} ${pct}%, transparent)`;
}

// Dates arrive as date-only ISO ("2026-05-19") OR as short labels ("W17",
// "Mon Jun 16", "Thu", "Today"). Cadence values are calendar dates, not
// timestamps, so render a date-only "Mon DD" — matching the prototype's
// "Jun 11" copy — and parse the Y-M-D parts directly to avoid the UTC-midnight
// timezone shift that `new Date("YYYY-MM-DD")` (and thus formatDate) introduces.
// A full ISO timestamp falls back to the app's formatDate(); short labels pass
// through untouched. Tolerant of null/empty.
const _CAD_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function cadenceDate(value) {
  if (value === null || value === undefined || value === '') return '';
  const s = String(value);
  const dateOnly = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnly) {
    const mo = _CAD_MONTHS[parseInt(dateOnly[2], 10) - 1] || dateOnly[2];
    return `${mo} ${parseInt(dateOnly[3], 10)}`;
  }
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) return formatDate(s); // full timestamp
  return s; // short label (W17, "Mon Jun 16", "Thu", "Today")
}

async function fetchCadence() {
  const view = document.getElementById('cadence-view');
  try {
    const res = await fetch(`${API}/cadence`);
    const data = await res.json();
    cadenceData = data;
    renderCadence();
  } catch (err) {
    view.innerHTML = `<div class="cadence-empty" style="color:var(--danger)">Failed to load cadence: ${escapeHtml(err.message)}</div>`;
  }
}

// Toggle a program's expanded state and re-render in place.
function toggleCadenceRow(id) {
  if (cadenceExpanded.has(id)) cadenceExpanded.delete(id);
  else cadenceExpanded.add(id);
  renderCadence();
}

function renderCadence() {
  const view = document.getElementById('cadence-view');
  if (!cadenceData) return;
  const families = cadenceData.families || [];

  let html = `<div class="cadence-page">
    <div class="cadence-header">
      <h2 class="cadence-title">Cadence</h2>
      <p class="cadence-subtitle">The standing loops your TPM runs in the background — where each one stands, at a glance.</p>
    </div>`;

  if (families.length === 0) {
    html += `<div class="cadence-empty">No standing loops yet.</div></div>`;
    view.innerHTML = html;
    return;
  }

  for (const fam of families) {
    html += `<section class="cadence-family">
      <div class="cadence-family-label">${escapeHtml(fam.label || fam.id)}</div>
      <div class="cadence-lane">`;
    const programs = fam.programs || [];
    for (let i = 0; i < programs.length; i++) {
      html += cadenceRow(programs[i], i > 0);
    }
    html += `</div></section>`;
  }

  html += `</div>`;
  view.innerHTML = html;
}

// One program row: collapsed 3-column header + (optionally) the expanded panel.
function cadenceRow(p, hasBorder) {
  const expanded = cadenceExpanded.has(p.id);
  const tone = cadenceTone(p.drift);
  const model = p.model;

  const chevTransform = expanded ? 'rotate(90deg)' : 'none';
  const chev = `<span class="cadence-chev" style="transform:${chevTransform};"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4l4 4-4 4"></path></svg></span>`;

  const col2 = cadenceColumn2(p, tone);
  const healthWord = cadenceHealthWord(p.drift);

  let row = `<div class="cadence-row${hasBorder ? ' has-border' : ''}${expanded ? ' is-open' : ''}">
    <div class="cadence-row-head" onclick="toggleCadenceRow('${escapeAttr(p.id)}')">
      <div class="cadence-name">
        ${chev}
        <span class="cadence-name-text">${escapeHtml(p.name || '')}</span>
      </div>
      <div class="cadence-col2">${col2}</div>
      <div class="cadence-health">
        <span class="cadence-health-word" style="color:${tone};">${escapeHtml(healthWord)}</span>
      </div>
    </div>`;

  if (expanded) row += cadenceExpandedPanel(p, tone);

  row += `</div>`;
  return row;
}

// ─── Column 2 — branches on model ───────────────────────────────────
function cadenceColumn2(p, tone) {
  if (p.model === 'pipeline') return cadenceStepper(p, tone);
  if (p.model === 'target') return cadenceMetric(p, tone);
  // cycle | register — a one-line status string
  return `<span class="cadence-status-line">${escapeHtml(p.status_line || '')}</span>`;
}

function cadenceStepper(p, tone) {
  const phases = p.phases || [];
  const n = phases.length;
  const current = p.current || 0;
  const reachPct = n > 1 ? current / (n - 1) : 0;

  let dots = '';
  for (let i = 0; i < n; i++) {
    const ph = phases[i];
    const state = i < current ? 'past' : i === current ? 'current' : 'future';
    let dotStyle, labelStyle;
    if (state === 'past') {
      dotStyle = `width:9px;height:9px;border-radius:50%;background:${cadenceSoft('var(--text-dim)', 70)};`;
      labelStyle = `font-size:10.5px;color:var(--text-dim);white-space:nowrap;`;
    } else if (state === 'current') {
      dotStyle = `width:11px;height:11px;border-radius:50%;background:${tone};box-shadow:0 0 0 4px ${cadenceSoft(tone, 18)};margin-top:-1px;`;
      labelStyle = `font-size:11px;color:${tone};font-weight:700;white-space:nowrap;`;
    } else {
      dotStyle = `width:9px;height:9px;border-radius:50%;background:var(--surface);border:1.5px solid var(--border);`;
      labelStyle = `font-size:10.5px;color:var(--text-dim);opacity:0.65;white-space:nowrap;`;
    }
    dots += `<div class="cadence-phase">
      <span class="cadence-phase-dot" style="${dotStyle}"></span>
      <span class="cadence-phase-label" style="${labelStyle}">${escapeHtml(ph.label || '')}</span>
    </div>`;
  }

  return `<div class="cadence-stepper">
    <div class="cadence-stepper-track"></div>
    <div class="cadence-stepper-reach" style="width:calc((100% - 10px) * ${reachPct});"></div>
    <div class="cadence-stepper-dots">${dots}</div>
  </div>`;
}

function cadenceMetric(p, tone) {
  const m = p.metric || {};
  const unit = m.unit || '';
  // Big-number color: holding→text, drifting→warning, broken→danger.
  const mtone = p.drift === 'broken' ? 'var(--danger)' : p.drift === 'drifting' ? 'var(--warning)' : 'var(--text)';
  // Guard missing numeric fields: ?? (not ||) so 0 stays a valid value, and a
  // null/undefined actual/target shows '-' (no stray unit) instead of "null%".
  const big = m.actual == null ? '-' : `${m.actual}${unit}`;
  const targetStr = m.target == null ? '-' : `${m.target}${unit}`;
  // delta_str arrives pre-formatted from the server (ASCII hyphen). Color:
  // positive → success; negative → drift tone.
  const delta = p.delta_str || '';
  const negative = delta.charAt(0) === '-';
  const deltaColor = negative ? tone : 'var(--success)';

  return `<div class="cadence-metric">
    <span class="cadence-metric-big" style="color:${mtone};">${escapeHtml(big)}</span>
    <span class="cadence-metric-target">target ${escapeHtml(targetStr)}</span>
    <span class="cadence-metric-delta" style="color:${deltaColor};">${escapeHtml(delta)}</span>
  </div>`;
}

// ─── Expanded panel ─────────────────────────────────────────────────
function cadenceExpandedPanel(p, tone) {
  const intent = p.intent ? `<p class="cadence-intent">${escapeHtml(p.intent)}</p>` : '';

  const histLabel = p.model === 'pipeline' ? 'Phase history'
    : p.model === 'target' ? 'Predicted vs actual'
    : p.model === 'cycle' ? 'Recent periods'
    : 'Open items';

  const history = cadenceHistory(p, tone);
  const checkpoints = cadenceCheckpoints(p);
  const activity = cadenceActivity(p);
  const footer = cadenceFooter(p);

  return `<div class="cadence-expand">
    ${intent}
    <div class="cadence-body">
      <div class="cadence-body-left">
        <div class="cadence-block">
          <div class="cadence-eyebrow">${escapeHtml(histLabel)}</div>
          ${history}
        </div>
        <div class="cadence-block">
          <div class="cadence-eyebrow">Checkpoints</div>
          ${checkpoints}
        </div>
      </div>
      <div class="cadence-body-right">
        <div class="cadence-eyebrow">Activity</div>
        ${activity}
      </div>
    </div>
    ${footer}
  </div>`;
}

function cadenceHistory(p, tone) {
  if (p.model === 'pipeline') return cadencePhaseHistory(p, tone);
  if (p.model === 'target') return cadenceChart(p, tone);
  if (p.model === 'cycle') return cadencePeriods(p);
  return cadenceItems(p);
}

function cadencePhaseHistory(p, tone) {
  const phases = p.phases || [];
  const current = p.current || 0;
  let rows = '';
  for (let i = 0; i < phases.length; i++) {
    const ph = phases[i];
    const state = i < current ? 'past' : i === current ? 'current' : 'future';
    let meta, dotBg, metaColor = 'var(--text-dim)', border = 'none';
    const entered = cadenceDate(ph.entered);
    if (state === 'past') {
      meta = entered ? `entered ${entered} · done` : 'done';
      dotBg = cadenceSoft('var(--text-dim)', 70);
    } else if (state === 'current') {
      meta = (entered ? `entered ${entered}` : 'in progress') + (ph.window ? ` · ${ph.window}-day window` : ' · no fixed window');
      dotBg = tone;
      metaColor = p.drift === 'holding' ? 'var(--text-dim)' : tone;
    } else {
      meta = ph.window ? `${ph.window}-day window` : 'not started';
      dotBg = 'transparent';
      border = '1.5px solid var(--border)';
    }
    rows += `<div class="cadence-phase-hist">
      <span class="cadence-hist-dot" style="background:${dotBg};border:${border};"></span>
      <span class="cadence-hist-label">${escapeHtml(ph.label || '')}</span>
      <span class="cadence-hist-meta" style="color:${metaColor};">${escapeHtml(meta)}</span>
    </div>`;
  }
  return `<div class="cadence-phase-hist-list">${rows}</div>`;
}

function cadenceChart(p, tone) {
  const s = p.series || {};
  // Stroke (actual line + last dot): holding→success, drifting→warning,
  // broken→danger. (Predicted line always text-dim dashed; band text-dim @10%.)
  const stroke = p.drift === 'broken' ? 'var(--danger)' : p.drift === 'drifting' ? 'var(--warning)' : 'var(--success)';
  return `<div class="cadence-chart">
    <svg viewBox="0 0 300 66" preserveAspectRatio="none" class="cadence-chart-svg">
      <polygon points="${escapeAttr(s.band || '')}" fill="var(--text-dim)" opacity="0.10"></polygon>
      <polyline points="${escapeAttr(s.predPts || '')}" fill="none" stroke="var(--text-dim)" stroke-width="1.4" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"></polyline>
      <polyline points="${escapeAttr(s.actPts || '')}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"></polyline>
      ${s.lastX != null && s.lastX !== '' ? `<circle cx="${escapeAttr(s.lastX)}" cy="${escapeAttr(s.lastY)}" r="3" fill="${stroke}"></circle>` : ''}
    </svg>
    <div class="cadence-chart-legend">
      <span class="cadence-legend-item"><span class="cadence-legend-dash"></span>Expected</span>
      <span class="cadence-legend-item"><span class="cadence-legend-line" style="background:${stroke};"></span>Actual</span>
    </div>
  </div>`;
}

function cadencePeriods(p) {
  const periods = p.periods || [];
  const sm = { sent: 'var(--success)', late: 'var(--warning)', missed: 'var(--danger)' };
  let cells = '';
  for (const b of periods) {
    const c = sm[b.s] || 'var(--text-dim)';
    cells += `<div class="cadence-period">
      <span class="cadence-period-cell" style="background:${cadenceSoft(c, 80)};border:1px solid ${cadenceSoft(c, 90)};" title="${escapeAttr(`${b.w}: ${b.s}`)}"></span>
      <span class="cadence-period-label">${escapeHtml(b.w || '')}</span>
    </div>`;
  }
  return `<div class="cadence-periods">${cells}</div>`;
}

function cadenceItems(p) {
  const items = p.items || [];
  const policy = p.policy;
  let rows = '';
  for (const it of items) {
    const age = it.age;
    // Missing age → neutral --text-dim and a bare '-' (no trailing 'd');
    // a present age (incl. 0) keeps its color comparison and 'Nd' label.
    const hasAge = age != null;
    let ageColor = 'var(--text-dim)';
    if (hasAge && policy != null && age > policy) ageColor = 'var(--danger)';
    else if (hasAge && age > 14) ageColor = 'var(--warning)';
    const ageStr = hasAge ? `${escapeHtml(String(age))}d` : '-';
    rows += `<div class="cadence-item">
      <span class="cadence-item-name">${escapeHtml(it.name || '')}</span>
      <span class="cadence-item-meta">${escapeHtml(it.owner || '')} · <span class="cadence-item-age" style="color:${ageColor};">${ageStr}</span></span>
    </div>`;
  }
  return `<div class="cadence-items">${rows}</div>`;
}

function cadenceCheckpoints(p) {
  const checkpoints = p.checkpoints || [];
  let rows = '';
  for (const c of checkpoints) {
    const stColor = c.status === 'missed' ? 'var(--danger)' : c.status === 'met' ? 'var(--success)' : 'var(--text-dim)';
    const dueColor = c.status === 'missed' ? 'var(--danger)' : 'var(--text-muted)';
    rows += `<div class="cadence-checkpoint">
      <div class="cadence-checkpoint-top">
        <span class="cadence-checkpoint-label">${escapeHtml(c.label || '')}</span>
        <span class="cadence-checkpoint-due" style="color:${dueColor};">${escapeHtml(cadenceDate(c.due))}</span>
        <span class="cadence-checkpoint-status" style="color:${stColor};">${escapeHtml(c.status || '')}</span>
      </div>
      <div class="cadence-checkpoint-sub">measured by ${escapeHtml(c.instrument || '')}</div>
    </div>`;
  }
  return `<div class="cadence-checkpoints">${rows}</div>`;
}

function cadenceActivity(p) {
  const activity = p.activity || [];
  let rows = '';
  for (const a of activity) {
    const tag = a.tag ? `<span class="cadence-activity-tag"> · ${escapeHtml(a.tag)}</span>` : '';
    rows += `<div class="cadence-activity-row">
      <span class="cadence-activity-date">${escapeHtml(cadenceDate(a.date))}</span>
      <span class="cadence-activity-text">${escapeHtml(a.text || '')}${tag}</span>
    </div>`;
  }
  return `<div class="cadence-activity">${rows}</div>`;
}

function cadenceFooter(p) {
  const bindings = p.bindings || [];
  let bind = '';
  for (const b of bindings) {
    const dotColor = b.health === 'ok' ? 'var(--success)' : 'var(--danger)';
    const text = `${b.role || ''} → ${b.anchor || ''} · checked ${cadenceDate(b.last)}`;
    bind += `<span class="cadence-binding"><span class="cadence-binding-dot" style="background:${dotColor};"></span>${escapeHtml(text)}</span>`;
  }
  const cadenceWord = p.cadence || 'as needed';
  const lastRun = cadenceDate(p.last_run);
  const footerStr = lastRun ? `Checked ${escapeHtml(cadenceWord)} · last run ${escapeHtml(lastRun)}` : `Checked ${escapeHtml(cadenceWord)}`;
  return `<div class="cadence-footer">
    ${bind}
    <span class="cadence-footer-spacer"></span>
    <span class="cadence-footer-str">${footerStr}</span>
  </div>`;
}
