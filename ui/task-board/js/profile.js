// ─── Profile / Config room ──────────────────────────────────────────────
// A calm, sectioned, form-light room — "settings for a chief of staff," not a
// config panel. Everything reads/writes plain profile/*.yaml + voice/*.md
// underneath; the user only ever sees friendly fields. Lives in the Engine tab.
// Built against the real /api/profile shapes (Task 4.1–4.3).

let _profile = null;
let _packPickerOpen = false;

const PF_TZS = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Singapore'];

const PF_HARNESS_COPY = {
  claude: ‘Claude CLI (claude -p) with the Claude model family. Full MCP support, fairway hooks, per-tool allowlists.’,
  codex: ‘Codex CLI (codex exec) with the Terra model family. No MCP, no fairway hooks — best for local-only work.’,
};

const PF_POSTURE_COPY = {
  low: ‘Use the cheapest model that still does a great job. Magnolia reaches for a stronger model only when a task clearly needs it.’,
  balanced: ‘A sensible default — lighter models for routine work, stronger ones where quality matters most.’,
  high: ‘Reach for the strongest model first. Best quality, higher cost — good when you’re leaning on Magnolia heavily.’,
};

async function renderProfile() {
  const view = document.getElementById('profile-view');
  if (!view) return;
  if (!_profile) view.innerHTML = `<div class="loading">Loading…</div>`;
  try {
    const res = await fetch(`${API}/profile`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _profile = await res.json();
  } catch (err) {
    view.innerHTML = `<div class="loading">Couldn’t load your profile: ${escapeHtml(err.message)}</div>`;
    return;
  }
  view.innerHTML = _profileHtml(_profile);
}

function _profileHtml(p) {
  return `
  <div class="profile-head">
    <h3>Profile</h3>
    <p class="profile-sub">How Magnolia knows you, what it’s connected to, and how it sounds on your behalf. Plain settings — there are no files to open here.</p>
  </div>
  ${_pfIdentity(p.identity)}
  ${_pfIntegrations(p.integrations)}
  ${_pfVoice(p.voice)}
  ${_pfPacks(p.packs)}
  ${_pfHarness(p.harness)}
  ${_pfPosture(p.model_posture)}`;
}

/* ── 1 · Identity ─────────────────────────────────────────────────────── */
function _pfIdentity(id) {
  const opt = (arr, val) => arr.map(o => `<option value="${escapeHtml(o)}" ${o === val ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('');
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Identity</span><span class="pf-saved" id="pf-saved-identity">Saved</span></div>
    <div class="pf-fields">
      <div class="pf-field"><label>Name</label><input class="pf-input" id="pf-name" value="${escapeHtml(id.name || '')}"></div>
      <div class="pf-field"><label>Email</label><input class="pf-input" id="pf-email" value="${escapeHtml(id.email || '')}"></div>
      <div class="pf-field"><label>Company</label><input class="pf-input" id="pf-company" value="${escapeHtml(id.company || '')}"></div>
      <div class="pf-field"><label>Timezone</label><select class="pf-select" id="pf-timezone">${opt(PF_TZS, id.timezone)}</select></div>
    </div>
    <div class="pf-actions"><button class="btn btn-primary" onclick="pfSaveIdentity()">Save</button></div>
  </section>`;
}

async function pfSaveIdentity() {
  const body = {
    name: document.getElementById('pf-name').value.trim(),
    email: document.getElementById('pf-email').value.trim(),
    company: document.getElementById('pf-company').value.trim(),
    timezone: document.getElementById('pf-timezone').value,
  };
  await fetch(`${API}/profile/identity`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  Object.assign(_profile.identity, body);
  _flashSaved('pf-saved-identity');
}

/* ── 2 · Integrations (the heart of the room) ─────────────────────────── */
function _pfIntegrations(ints) {
  let cats = '';
  for (const [key, cat] of Object.entries(ints)) {
    let rows = '';
    for (const o of cat.options) {
      const isActive = cat.active === o.id;
      let btn = '';
      if (o.status === 'reauth') btn = `<button class="pf-fix reauth" onclick="event.stopPropagation();pfFix('${o.id}')">Re-authorize</button>`;
      else if (o.status === 'available') btn = `<button class="pf-fix available" onclick="event.stopPropagation();pfFix('${o.id}')">Connect</button>`;
      const showDetail = (o.status === 'ok' || o.status === 'reauth') && o.detail;
      rows += `<div class="pf-provider ${isActive ? 'active' : ''} status-${o.status}" ${o.status === 'ok' ? `onclick="pfSetIntegration('${key}','${o.id}')"` : ''}>
        <span class="pf-dot ${o.status}"></span>
        <span class="pf-provider-main">
          <span class="pf-provider-name">${escapeHtml(o.label)}${isActive ? '<span class="pf-active-tag">Active</span>' : ''}</span>
          ${showDetail ? `<span class="pf-provider-detail">${escapeHtml(o.detail)}</span>` : ''}
        </span>
        ${btn}
      </div>`;
    }
    // Degraded dependent control, shown where a capability the category powers is down.
    let locked = '';
    if (key === 'project_management') {
      const jira = cat.options.find(o => o.id === 'jira');
      if (jira && jira.status !== 'ok') {
        locked = `<div class="pf-locked">
          <span class="pf-locked-icon">${svgIcon('needsHuman')}</span>
          <span class="pf-locked-text"><b>Publishing drafts to Jira is paused.</b> Jira needs re-auth before Magnolia can push tickets.</span>
          <button class="pf-locked-fix" onclick="pfFix('jira')">Walk me through it</button>
        </div>`;
      }
    }
    cats += `<div class="pf-int-cat"><div class="pf-int-cat-head">${escapeHtml(cat.label)}</div><div class="pf-providers">${rows}</div>${locked}</div>`;
  }
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Integrations</span><span class="pf-section-hint">Tools Magnolia works through</span></div>
    ${cats}
  </section>`;
}

async function pfSetIntegration(category, id) {
  await fetch(`${API}/profile/integrations/${category}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: id }) });
  _profile.integrations[category].active = id;
  document.getElementById('profile-view').innerHTML = _profileHtml(_profile);
}

function pfFix(capability) {
  // The Doctor flow is conversational and lives in Claude Code — there is no
  // fix endpoint. Show a calm inline note on/near the control pointing the user
  // there. Re-authorize / Connect / Walk me through it all route here.
  const cap = capability.charAt(0).toUpperCase() + capability.slice(1);
  const msg = `Reconnect ${escapeHtml(cap)} in Claude Code — type <code>fix ${escapeHtml(capability)}</code> and Magnolia will walk you through it.`;
  // Find the control the user clicked and drop the note right after it.
  const btn = event && event.target ? event.target.closest('.pf-fix, .pf-locked-fix') : null;
  const host = btn ? (btn.closest('.pf-locked') || btn.closest('.pf-provider') || btn.parentElement) : null;
  if (host) {
    let note = host.parentElement.querySelector(':scope > .pf-fix-note');
    if (!note) {
      note = document.createElement('div');
      note.className = 'pf-fix-note';
      host.insertAdjacentElement('afterend', note);
    }
    note.innerHTML = msg;
  }
}

/* ── 3 · Voice ────────────────────────────────────────────────────────── */
// Two stacked editors — Teams voice, then Email voice — each with the big-field
// real estate. Saves both together as {teams, email}. Mirrors the split that
// the backend and the onboarding flow keep.
function _pfVoice(voice) {
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Teams voice</span><span class="pf-section-hint">How you sound in Teams</span></div>
    <p class="pf-voice-intro">Magnolia writes your Teams messages as you. Edit this like a note to a new assistant; the more you tell it, the more every draft sounds like you on the first pass.</p>
    <textarea class="pf-textarea pf-voice-text" id="pf-voice-teams">${escapeHtml(voice.teams || '')}</textarea>
    <div class="pf-actions"><button class="btn btn-primary" onclick="pfSaveVoice()">Save voice</button><button class="pf-regen" onclick="pfRegen('teams')">${svgIcon('cron')}Regenerate from history</button><span class="pf-saved" id="pf-saved-voice-teams">Saved</span></div>
  </section>
  <section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Email voice</span><span class="pf-section-hint">How you sound over email</span></div>
    <p class="pf-voice-intro">Magnolia writes your email as you. Edit this like a note to a new assistant; the more you tell it, the more every draft sounds like you on the first pass.</p>
    <textarea class="pf-textarea pf-voice-text" id="pf-voice-email">${escapeHtml(voice.email || '')}</textarea>
    <div class="pf-actions"><button class="btn btn-primary" onclick="pfSaveVoice()">Save voice</button><button class="pf-regen" onclick="pfRegen('email')">${svgIcon('cron')}Regenerate from history</button><span class="pf-saved" id="pf-saved-voice-email">Saved</span></div>
  </section>`;
}

async function pfSaveVoice() {
  const teams = document.getElementById('pf-voice-teams').value;
  const email = document.getElementById('pf-voice-email').value;
  await fetch(`${API}/profile/voice`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ teams, email }) });
  _profile.voice = { teams, email };
  _flashSaved('pf-saved-voice-teams');
  _flashSaved('pf-saved-voice-email');
}

function pfRegen(which) {
  // Stand-in for "regenerate from my recent messages" — refreshes the draft.
  const ta = document.getElementById(which === 'email' ? 'pf-voice-email' : 'pf-voice-teams');
  if (!ta) return;
  if (which === 'email') {
    ta.value = "This is how I sound over email. Write as me.\n\nTone\nWarm and direct. Lead with the decision or the ask, then the why. Short sentences, first person.\n\nEmail\nOpen with the point — no windup. Drop “just wanted to”, “circling back”, and “per my last”. One ask per email. If it’s bad news, say it in the first line, then explain. Sign off “Thanks, Jay”.";
  } else {
    ta.value = "This is how I sound in Teams. Write as me.\n\nTone\nWarm and direct. Get to it in the first sentence. Lowercase is fine. A quick “nice work” when it’s earned. “We” for the team, “I” for my own calls.";
  }
  ta.focus();
}

/* ── 4 · Skill packs ──────────────────────────────────────────────────── */
function _pfPacks(packs) {
  const chips = packs.active.map(id => {
    const meta = packs.available.find(a => a.id === id);
    return `<span class="pf-pack">${escapeHtml(meta ? meta.label : id)}</span>`;
  }).join('');
  const options = packs.available.map(a => {
    const on = packs.active.includes(a.id);
    return `<div class="pf-pack-option ${on ? 'active' : ''}" onclick="pfTogglePack('${a.id}')">
      <span class="pf-pack-option-main">
        <span class="pf-pack-option-name">${escapeHtml(a.label)}</span>
        <span class="pf-pack-option-desc">${escapeHtml(a.description)}</span>
      </span>
      <span class="pf-pack-check">${svgIcon('done')}</span>
    </div>`;
  }).join('');
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Skill packs</span><span class="pf-section-hint">What Magnolia knows how to do</span></div>
    <div class="pf-packs">${chips}<button class="pf-pack-add" onclick="pfTogglePicker()">+ Add or swap</button></div>
    <div class="pf-pack-picker" id="pf-pack-picker" style="display:${_packPickerOpen ? 'flex' : 'none'}">${options}</div>
  </section>`;
}

function pfTogglePicker() {
  _packPickerOpen = !_packPickerOpen;
  const el = document.getElementById('pf-pack-picker');
  if (el) el.style.display = _packPickerOpen ? 'flex' : 'none';
}

async function pfTogglePack(id) {
  const active = new Set(_profile.packs.active);
  if (active.has(id)) { if (active.size > 1) active.delete(id); } else active.add(id);
  _profile.packs.active = Array.from(active);
  await fetch(`${API}/profile/packs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: _profile.packs.active }) });
  document.getElementById('profile-view').innerHTML = _profileHtml(_profile);
}

/* ── 4b · CLI Harness ─────────────────────────────────────────────────── */
function _pfHarness(h) {
  if (!h) return '';
  const seg = (h.available || ['claude', 'codex']).map(id =>
    `<button class="${h.active === id ? 'active' : ''}" onclick="pfSetHarness('${id}')">${id[0].toUpperCase() + id.slice(1)}</button>`).join('');
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">CLI Harness</span><span class="pf-section-hint">Which CLI powers Magnolia</span></div>
    <div class="pf-posture">
      <div class="pf-seg-row"><div class="pf-seg" id="pf-seg-harness">${seg}</div><span class="pf-saved" id="pf-saved-harness">Saved</span></div>
      <div class="pf-posture-explain" id="pf-harness-explain">${PF_HARNESS_COPY[h.active] || ''}</div>
    </div>
  </section>`;
}

function _pfPaintHarness(id) {
  document.querySelectorAll('#pf-seg-harness button').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === id));
  const ex = document.getElementById('pf-harness-explain');
  if (ex) ex.textContent = PF_HARNESS_COPY[id] || '';
}

async function pfSetHarness(h) {
  if (!_profile || !_profile.harness) return;
  const prev = _profile.harness.active;
  if (h === prev) return;
  _profile.harness.active = h;
  _pfPaintHarness(h);
  try {
    const res = await fetch(`${API}/config/harness`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: h }) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const pr = await fetch(`${API}/profile`);
    if (pr.ok) {
      _profile = await pr.json();
      const list = document.getElementById('pf-workers-list');
      if (list) list.innerHTML = _pfWorkerRows(_profile.model_posture.workers);
    }
    _flashSaved('pf-saved-harness');
  } catch (err) {
    _profile.harness.active = prev;
    _pfPaintHarness(prev);
  }
}

/* ── 5 · Model posture ────────────────────────────────────────────────── */
// Each worker declares a tier (light/standard/deep); the resolved model is the
// tier shifted by the current posture (low −1 / balanced 0 / high +1) and
// clamped. We show both — the tier is the worker's intent, the model is what
// actually runs, so it visibly changes when the posture toggle flips.
function _pfWorkerRows(workers) {
  return (workers || []).map(w =>
    `<div class="pf-worker"><span class="pf-worker-name">${escapeHtml(w.name)}</span>` +
    `<span class="pf-worker-meta"><span class="pf-tier ${escapeHtml(w.tier)}">${escapeHtml(w.tier)}</span>` +
    `<span class="pf-worker-model">${escapeHtml(w.model || '—')}</span></span></div>`).join('');
}

function _pfPosture(mp) {
  const seg = ['low', 'balanced', 'high'].map(l =>
    `<button class="${mp.level === l ? 'active' : ''}" onclick="pfSetPosture('${l}')">${l[0].toUpperCase() + l.slice(1)}</button>`).join('');
  return `<section class="pf-section">
    <div class="pf-section-head"><span class="pf-section-title">Model posture</span><span class="pf-section-hint">Cost ↔ horsepower</span></div>
    <div class="pf-posture">
      <div class="pf-seg-row"><div class="pf-seg" id="pf-seg">${seg}</div><span class="pf-saved" id="pf-saved-posture">Saved</span></div>
      <div class="pf-posture-explain" id="pf-posture-explain">${PF_POSTURE_COPY[mp.level]}</div>
      <div class="pf-workers-label">Per worker</div>
      <div class="pf-workers" id="pf-workers-list">${_pfWorkerRows(mp.workers)}</div>
    </div>
  </section>`;
}

function _pfPaintPosture(level) {
  document.querySelectorAll('#pf-seg button').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === level));
  const ex = document.getElementById('pf-posture-explain');
  if (ex) ex.textContent = PF_POSTURE_COPY[level];
}

async function pfSetPosture(level) {
  const prev = _profile.model_posture.level;
  if (level === prev) return;
  // Paint the new level optimistically, then persist. On success re-fetch so the
  // per-worker resolved models reflect the new posture (e.g. light→standard:
  // Haiku jumps to Sonnet). On failure, roll the UI back to where it was.
  _profile.model_posture.level = level;
  _pfPaintPosture(level);
  try {
    const res = await fetch(`${API}/profile/model-posture`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ level }) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const pr = await fetch(`${API}/profile`);
    if (pr.ok) {
      _profile = await pr.json();
      const list = document.getElementById('pf-workers-list');
      if (list) list.innerHTML = _pfWorkerRows(_profile.model_posture.workers);
    }
    _flashSaved('pf-saved-posture');
  } catch (err) {
    _profile.model_posture.level = prev;
    _pfPaintPosture(prev);
  }
}

/* ── shared ───────────────────────────────────────────────────────────── */
function _flashSaved(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 1600);
}
