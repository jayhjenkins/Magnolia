// ─── Activity Surface ───────────────────────────────────────────────────
// Reverse-chron list of archived (done/cancelled) tasks from /api/activity.
// Clicking a row opens the task in a read-only workspace view (output tiles,
// description, activity log, judge score, 👍/👎 — no write actions, no chat).
// The "Open output" link is a direct shortcut that does not open the modal.
// Client-side substring filter over id+title+domain+queue; fetched data is
// cached so filtering never refetches.

let _activityData = [];

async function renderActivity() {
  const view = document.getElementById('activity-view');
  if (!view) return;

  view.innerHTML = `<div class="loading">Loading activity…</div>`;

  try {
    const res = await fetch(`${API}/activity?limit=1000`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _activityData = await res.json();
  } catch (err) {
    view.innerHTML = `<div class="loading">Error loading activity: ${escapeHtml(err.message)}</div>`;
    toast(`Activity failed to load: ${err.message}`);
    return;
  }

  view.innerHTML = `
    <div class="activity-filter-bar">
      <input type="text" id="activity-filter" class="activity-filter-input"
             placeholder="Filter by id, title, domain, queue…"
             oninput="_renderActivityRows(this.value)">
    </div>
    <div class="activity-table">
      <div class="activity-head">
        <span>Date</span>
        <span>ID</span>
        <span>Title</span>
        <span>Type</span>
        <span>Domain</span>
        <span>Output</span>
      </div>
      <div class="activity-rows" id="activity-rows"></div>
    </div>
  `;

  _renderActivityRows('');
}

function _renderActivityRows(filterStr) {
  const rowsEl = document.getElementById('activity-rows');
  if (!rowsEl) return;

  const needle = (filterStr || '').trim().toLowerCase();
  const rows = needle
    ? _activityData.filter(t => {
        const hay = `${t.id || ''} ${t.title || ''} ${t.domain || ''} ${t.queue || ''}`.toLowerCase();
        return hay.includes(needle);
      })
    : _activityData;

  if (rows.length === 0) {
    rowsEl.innerHTML = `<div class="now-empty">No activity matches that filter.</div>`;
    return;
  }

  rowsEl.innerHTML = rows.map(_renderActivityRow).join('');
}

function _renderActivityRow(t) {
  const queue = t.queue || '';
  const typeClass = ['human', 'agent', 'collab', 'waiting'].includes(queue) ? `atype-${queue}` : '';
  const typeCell = queue
    ? `<span class="activity-type ${typeClass}">${escapeHtml(queue)}</span>`
    : `<span class="activity-type activity-empty-cell">—</span>`;
  const domainCell = t.domain
    ? `<span class="activity-domain">${escapeHtml(t.domain)}</span>`
    : `<span class="activity-domain activity-empty-cell">—</span>`;

  let link;
  if (t.agent_output) {
    const v = String(t.agent_output).trim();
    const urlMatch = v.match(/https?:\/\/[^\s)]+/);
    if (urlMatch) {
      link = `<a class="activity-link" href="${escapeHtml(urlMatch[0])}" target="_blank" rel="noopener" title="${escapeHtml(urlMatch[0])}" onclick="event.stopPropagation()">Open output</a>`;
    } else {
      link = `<a class="activity-link" href="${obsidianUri(v)}" title="Open in Obsidian" onclick="event.stopPropagation()">Open output</a>`;
    }
  } else if (t.sharepoint_url) {
    link = `<a class="activity-link" href="${escapeHtml(t.sharepoint_url)}" target="_blank" rel="noopener" title="Open in Word Online" onclick="event.stopPropagation()">Open output</a>`;
  } else {
    link = `<span class="activity-link activity-empty-cell">—</span>`;
  }

  return `
    <div class="activity-row card" onclick="openTask('${escapeHtml(t.id || '')}')">
      <span class="activity-date">${escapeHtml(formatDate(t.updated))}</span>
      <span class="activity-id">${escapeHtml(t.id || '')}</span>
      <span class="activity-title">${escapeHtml(t.title || '')}</span>
      ${typeCell}
      ${domainCell}
      ${link}
    </div>
  `;
}
