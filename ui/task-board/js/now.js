// ─── Now Surface ───────────────────────────────────────────────────────
// A vertical priority STACK. The card kinds that the user disposes of in one
// tap ride at the top in their own calm sections — Recommendations (the
// chief-of-staff's standup), the occasional trust moment (Graduation), and
// what the machine just handled (Receipts). Plain tasks fall into the
// attention lanes below via deriveAttentionState(task).lane:
//   Decide · Review · People · Agent queue (collapsed)
// Reuses renderCard(task, queue) from board.js so the modal verb matrix and
// the declarative card registry survive unchanged.

const PRIORITY_RANK = { critical: 0, high: 1, medium: 2, low: 3 };

function sortByPriority(tasks) {
  return tasks.slice().sort((a, b) => {
    const ra = PRIORITY_RANK[a.priority] ?? 99;
    const rb = PRIORITY_RANK[b.priority] ?? 99;
    return ra - rb;
  });
}

// Section with an optional subtitle + count in the header.
function renderNowSection(title, tasks, emptyText, opts) {
  opts = opts || {};
  const count = tasks.length;
  let html = `<section class="now-section">`;
  html += `<div class="now-section-header">`;
  html += `<span class="now-section-title"><span class="now-section-name">${title}</span>${opts.subtitle ? `<span class="now-section-sub">${opts.subtitle}</span>` : ''}</span>`;
  if (opts.showCount && count) html += `<span class="now-count">${count}</span>`;
  html += `</div>`;
  html += `<div class="now-section-body">`;
  if (count === 0) {
    html += `<div class="now-empty">${emptyText}</div>`;
  } else {
    sortByPriority(tasks).forEach(t => { html += renderCard(t, t.queue); });
  }
  html += `</div></section>`;
  return html;
}

// Recommendations — load-bearing, top of Now. The empty state is the most
// common, healthiest state, so it's designed as a quiet "all clear", never
// an empty-error.
function renderRecommendations(recs) {
  if (recs.length === 0) {
    return `<section class="now-section now-recs">
      <div class="now-section-header">
        <span class="now-section-title"><span class="now-section-name">Suggestions</span></span>
      </div>
      <div class="now-section-body">
        <div class="now-allclear">
          <span class="allclear-mark">${svgIcon('complete')}</span>
          <div class="allclear-copy">
            <div class="allclear-title">You’re all caught up</div>
            <div class="allclear-sub">Nothing needs your call right now. Magnolia will surface the next suggestion here when it’s ready.</div>
          </div>
        </div>
      </div>
    </section>`;
  }
  return renderNowSection(
    'Suggestions', recs,
    '',
    { subtitle: 'Changes Magnolia proposes — accept or set aside in one tap.', showCount: true });
}

function renderNow() {
  const view = document.getElementById('now-view');
  if (!view) return;

  const active = allTasks.filter(t => t.status !== 'cancelled');
  const recs = active.filter(t => t.card_type === 'recommendation');
  const grads = active.filter(t => t.card_type === 'graduation');
  const receipts = active.filter(t => t.card_type === 'receipt');
  const rest = active.filter(t => !['recommendation', 'graduation', 'receipt'].includes(t.card_type));

  const lanes = { review: [], decide: [], people: [], 'agent-queue': [] };
  rest.forEach(t => {
    const { lane } = deriveAttentionState(t);
    if (lanes[lane]) lanes[lane].push(t);
  });

  let html = '';

  // The dispose-in-one-tap surfaces, in order of weight.
  html += renderRecommendations(recs);

  if (grads.length) {
    html += renderNowSection(
      'Promotion Cycle', grads, '',
      { subtitle: 'A task-type has earned a step up the trust ladder.' });
  }

  if (receipts.length) {
    html += renderNowSection(
      'Recently handled', receipts, '',
      { subtitle: 'Done on your behalf — keep it, or undo.', showCount: true });
  }

  // The attention lanes (plain tasks).
  html += renderNowSection('Decide', lanes.decide, 'Nothing waiting on your approval.');
  html += renderNowSection('Review', lanes.review, 'Nothing to review.');
  html += renderNowSection('People', lanes.people, 'Inbox zero.');

  // Agent queue — collapsed by default; preserve open state across re-renders.
  const aq = lanes['agent-queue'];
  const aqWasOpen = view.querySelector('.now-agent-queue')?.open ?? false;
  html += `<details class="now-section now-agent-queue"${aqWasOpen ? ' open' : ''}>`;
  html += `<summary class="now-section-header"><span class="now-section-title"><span class="now-section-name">Agent queue</span></span>${aq.length ? `<span class="now-count">${aq.length}</span>` : ''}</summary>`;
  html += `<div class="now-section-body">`;
  if (aq.length === 0) {
    html += `<div class="now-empty">Agent queue is empty.</div>`;
  } else {
    sortByPriority(aq).forEach(t => { html += renderCard(t, t.queue); });
  }
  html += `</div></details>`;

  const scrollTop = view.scrollTop;
  view.innerHTML = html;
  view.scrollTop = scrollTop;
  // Re-apply any inline notices (e.g. a 409 the user is still reading) that the
  // full re-render would otherwise wipe.
  if (typeof reapplyCardNotices === 'function') reapplyCardNotices();
}
