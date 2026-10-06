// html-viewer.js - inline preview for HTML page outputs.
//
// The HTML sibling of markdown-editor.js. When a task's output is an .html page,
// the "Preview" tile in the task detail pane slides this overlay in over the
// LEFT pane of the split workspace (chat stays on the right). The page renders
// in a sandboxed iframe served by GET /artifact/<id> (opaque origin, no network).
// While open, a light poll picks up edits the chat makes to the file on disk and
// reloads the preview.
//
// The bar works with the file itself, for sharing: "Open folder" reveals it in
// the OS file manager (selected, ready to drag into Teams or an email), "Open
// full" opens it in the default browser straight from disk, and "Copy link"
// copies its file:// URL.
//
// It reuses the markdown editor's overlay chrome (.dt-editor / .dte-* classes,
// is-open / is-closing, has-editor on the pane) so both read as one system.
// Every selector here is scoped to .dt-htmlview so it never touches the md editor.
//
// Depends on globals: API, escapeHtml, svgIcon, toast (core.js / icons.js).

(function () {
  const POLL_MS = 2000;

  let viewTaskId = null;   // task whose page is open
  let viewGen = 0;         // bumps on every open/teardown; stale async work checks it
  let lastContent = '';    // last known on-disk content
  let fileUrl = '';        // file:// URL of the page on disk
  let pollTimer = null;
  let pollBusy = false;

  function q(sel) { return document.querySelector('.dt-htmlview ' + sel); }
  function artifactSrc(taskId) {
    return '/artifact/' + encodeURIComponent(taskId) + '?t=' + Date.now();
  }

  // -- Overlay --
  function buildOverlay(taskPane) {
    const ov = document.createElement('div');
    ov.className = 'dt-editor dt-htmlview';
    ov.innerHTML = `
      <div class="dte-bar">
        <button class="dte-back" type="button" aria-label="Back to task">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 5.5 8l4.5 4.5"/></svg>
          <span>Task</span>
        </button>
        <div class="dte-doc">
          <span class="dte-doc-icon">${svgIcon('doc')}</span>
          <span class="dte-doc-name"></span>
        </div>
        <div class="dte-spacer"></div>
        <div class="dth-actions">
          <button class="dth-btn dth-folder" type="button" disabled>Open folder</button>
          <button class="dth-btn dth-full" type="button" disabled>Open full</button>
          <button class="dth-btn dth-copy" type="button" disabled>Copy link</button>
        </div>
      </div>
      <div class="dth-body">
        <div class="dte-loading"><span class="dte-spin"></span><span>Opening the page...</span></div>
      </div>`;
    taskPane.appendChild(ov);
    ov.querySelector('.dte-back').addEventListener('click', closeHtmlViewer);
    ov.querySelector('.dth-folder').addEventListener('click', () => fileAction('reveal'));
    ov.querySelector('.dth-full').addEventListener('click', () => fileAction('open'));
    ov.querySelector('.dth-copy').addEventListener('click', copyLink);
    return ov;
  }

  function showMessage(body, msg) {
    body.innerHTML = `<div class="dth-empty">${escapeHtml(msg)}</div>`;
  }

  function mountFrame(body, taskId) {
    body.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.className = 'dth-frame';
    frame.title = 'HTML page preview';
    frame.setAttribute('sandbox', 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    frame.src = artifactSrc(taskId);
    body.appendChild(frame);
  }

  function reloadFrame() {
    const frame = q('.dth-frame');
    if (frame && viewTaskId) frame.src = artifactSrc(viewTaskId);
  }

  // -- Open --
  async function openHtmlViewer(taskId) {
    const taskPane = document.querySelector('#split-modal .task-pane');
    if (!taskPane || !taskId) return;
    if (viewTaskId || document.querySelector('.dt-htmlview')) destroyViewer();
    const gen = ++viewGen;
    viewTaskId = taskId;
    lastContent = '';
    fileUrl = '';

    const ov = buildOverlay(taskPane);
    taskPane.classList.add('has-editor');
    void ov.offsetWidth; // commit hidden baseline
    requestAnimationFrame(() => { if (gen === viewGen) ov.classList.add('is-open'); });

    let data = null, ok = false;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(taskId)}/output`);
      if (res.ok) { data = await res.json(); ok = true; }
    } catch (_) {}
    if (gen !== viewGen) return;

    const docPath = (data && data.path) || '';
    ov.querySelector('.dte-doc-name').textContent = docPath ? docPath.split('/').pop() : 'page.html';
    const body = ov.querySelector('.dth-body');

    if (!ok || !data || data.format !== 'html') { showMessage(body, "Couldn't open this page."); return; }
    if (data.exists === false) { showMessage(body, "This page hasn't been written yet."); return; }

    lastContent = data.content || '';
    fileUrl = data.file_url || '';
    ov.querySelectorAll('.dth-btn').forEach(b => { b.disabled = false; });
    if (!fileUrl) ov.querySelector('.dth-copy').disabled = true;
    mountFrame(body, taskId);
    startPoll(gen);
  }

  // -- File actions: reveal in the file manager / open in the default browser --
  async function fileAction(action) {
    if (!viewTaskId) return;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(viewTaskId)}/output/${action}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (_) {
      if (typeof toast === 'function') {
        toast(action === 'reveal' ? 'Couldn\'t open the folder.' : 'Couldn\'t open the page.');
      }
    }
  }

  // -- Copy link --
  // Copies the page's file:// URL so it opens straight from disk in a browser.
  // toast() only surfaces errors, so success flips the button label briefly.
  let copiedTimer = null;
  function copyLink() {
    const btn = q('.dth-copy');
    if (!fileUrl) return;
    if (!(navigator.clipboard && navigator.clipboard.writeText)) {
      if (typeof toast === 'function') toast('Couldn\'t copy the link - clipboard unavailable.');
      return;
    }
    navigator.clipboard.writeText(fileUrl)
      .then(() => {
        if (!btn || !btn.isConnected) return;
        btn.textContent = 'Copied';
        if (copiedTimer) clearTimeout(copiedTimer);
        copiedTimer = setTimeout(() => { btn.textContent = 'Copy link'; copiedTimer = null; }, 1200);
      })
      .catch(() => { if (typeof toast === 'function') toast('Couldn\'t copy the link.'); });
  }

  // -- Live reload (chat edits the file in place) --
  function startPoll(gen) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollOnce(gen), POLL_MS);
  }

  async function pollOnce(gen) {
    if (gen !== viewGen || !viewTaskId || pollBusy || document.hidden) return;
    const taskId = viewTaskId;
    pollBusy = true;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(taskId)}/output`);
      if (!res.ok) return;
      const data = await res.json();
      if (gen !== viewGen) return;
      if (!data || data.exists === false || typeof data.content !== 'string') return;
      if (data.content === lastContent) return;
      lastContent = data.content;
      reloadFrame();
    } catch (_) {
      // transient - next tick retries
    } finally {
      pollBusy = false;
    }
  }

  // -- Close / teardown --
  // The one place state is released: closeHtmlViewer's transition end, the
  // closeModal wrap, and openHtmlViewer's re-entrancy guard all land here.
  function destroyViewer() {
    viewGen++;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    viewTaskId = null;
    fileUrl = '';
    const ov = document.querySelector('.dt-htmlview');
    if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
    const taskPane = document.querySelector('#split-modal .task-pane');
    if (taskPane && !taskPane.querySelector('.dt-editor')) taskPane.classList.remove('has-editor');
  }

  function closeHtmlViewer() {
    const ov = document.querySelector('.dt-htmlview');
    if (!ov || ov.classList.contains('is-closing')) return;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    const gen = viewGen;
    const done = () => { if (gen === viewGen) destroyViewer(); };
    ov.classList.remove('is-open');
    ov.classList.add('is-closing');
    ov.addEventListener('transitionend', function onEnd(e) {
      if (e.target === ov && (e.propertyName === 'opacity' || e.propertyName === 'transform')) {
        ov.removeEventListener('transitionend', onEnd);
        done();
      }
    });
    setTimeout(done, 520); // safety net if transitionend doesn't fire
  }

  // Tear the viewer down first when the whole modal closes. Chains with the
  // markdown editor's wrapper (loaded earlier) - both run.
  const _origCloseModal = window.closeModal;
  window.closeModal = function () {
    if (document.querySelector('.dt-htmlview')) destroyViewer();
    if (typeof _origCloseModal === 'function') return _origCloseModal.apply(this, arguments);
  };

  // Esc closes the viewer first (one layer at a time). Registered on WINDOW in
  // the capture phase so it runs before markdown-editor.js's document-capture
  // handler and app.js's closeModal handler. Only acts when the viewer is open,
  // so Esc with the markdown editor open still reaches that editor, and steps
  // aside while a confirm dialog or quick-add is on top. Note: while the
  // sandboxed iframe has focus its key events stay inside the frame, so Esc
  // doesn't reach this handler - the back button is the way out then.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.querySelector('.confirm-overlay.active, .qa-overlay.active')) return;
    const ov = document.querySelector('.dt-htmlview.is-open');
    if (ov) { e.stopImmediatePropagation(); e.stopPropagation(); closeHtmlViewer(); }
  }, true);

  // Delegated tile-open, once for the page lifetime. Capture phase because the
  // tile's inline onclick stops propagation.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('.dt-html[data-output-task]');
    if (btn) openHtmlViewer(btn.dataset.outputTask);
  }, true);

  window.openHtmlViewer = openHtmlViewer;
  window.closeHtmlViewer = closeHtmlViewer;
})();
