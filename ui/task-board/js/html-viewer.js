// html-viewer.js - inline preview (and light source edit) for HTML page outputs.
//
// The HTML sibling of markdown-editor.js. When a task's output is an .html page,
// the "Preview" tile in the task detail pane slides this overlay in over the
// LEFT pane of the split workspace (chat stays on the right). The page renders
// in a sandboxed iframe served by GET /artifact/<id> (opaque origin, no network).
// A "Source" toggle swaps in a plain textarea that autosaves (debounced) through
// PUT /api/tasks/:id/output. While open, a light poll picks up edits the chat
// makes to the file on disk and reloads the preview.
//
// It reuses the markdown editor's overlay chrome (.dt-editor / .dte-* classes,
// is-open / is-closing, has-editor on the pane) so both read as one system.
// Every selector here is scoped to .dt-htmlview so it never touches the md editor.
//
// Depends on globals: API, escapeHtml, svgIcon, toast (core.js / icons.js).

(function () {
  const POLL_MS = 2000;
  const SAVE_DEBOUNCE_MS = 750;

  let viewTaskId = null;   // task whose page is open
  let viewGen = 0;         // bumps on every open/teardown; stale async work checks it
  let lastContent = '';    // last known on-disk content (fetched or saved)
  let docPath = '';
  let mode = 'preview';    // 'preview' | 'source'
  let pageExists = false;
  let pollTimer = null;
  let pollBusy = false;
  let saveTimer = null;
  let saving = false;
  let saveGen = 0;         // bumps when a PUT starts; polls begun earlier are discarded
  let lastPersist = null;  // promise of the latest close-time snapshot save
  let knownDisk = null;    // {taskId, content} last known on disk (outlives the viewer)

  function q(sel) { return document.querySelector('.dt-htmlview ' + sel); }
  function artifactSrc(taskId) {
    return '/artifact/' + encodeURIComponent(taskId) + '?t=' + Date.now();
  }

  // -- Overlay --
  function buildOverlay(taskPane, taskId) {
    const ov = document.createElement('div');
    ov.className = 'dt-editor dt-htmlview';
    const href = '/artifact/' + encodeURIComponent(taskId);
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
        <span class="dte-save" data-state="saved" hidden>
          <span class="dte-save-dot"></span>
          <span class="dte-save-text">Saved</span>
        </span>
        <div class="dth-actions">
          <button class="dth-btn dth-toggle" type="button" aria-pressed="false" disabled>Source</button>
          <a class="dth-btn dth-full" target="_blank" rel="noopener" href="${escapeHtml(href)}">Open full</a>
          <button class="dth-btn dth-copy" type="button">Copy path</button>
        </div>
      </div>
      <div class="dth-body">
        <div class="dte-loading"><span class="dte-spin"></span><span>Opening the page...</span></div>
      </div>`;
    taskPane.appendChild(ov);
    ov.querySelector('.dte-back').addEventListener('click', closeHtmlViewer);
    ov.querySelector('.dth-toggle').addEventListener('click', toggleMode);
    ov.querySelector('.dth-copy').addEventListener('click', copyPath);
    return ov;
  }

  function setSaveState(state) {
    const el = q('.dte-save');
    if (!el) return;
    el.dataset.state = state;
    const text = { saved: 'Saved', saving: 'Saving...', editing: 'Editing...', error: 'Save failed' }[state] || 'Saved';
    el.querySelector('.dte-save-text').textContent = text;
  }

  function showMessage(body, msg) {
    body.innerHTML = `<div class="dth-empty">${escapeHtml(msg)}</div>`;
  }

  function mountBody(body, taskId) {
    body.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.className = 'dth-frame';
    frame.title = 'HTML page preview';
    frame.setAttribute('sandbox', 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    frame.src = artifactSrc(taskId);
    const ta = document.createElement('textarea');
    ta.className = 'dth-source';
    ta.spellcheck = false;
    ta.hidden = true;
    ta.setAttribute('aria-label', 'HTML source');
    ta.addEventListener('input', scheduleSave);
    body.appendChild(frame);
    body.appendChild(ta);
  }

  function reloadFrame() {
    const frame = q('.dth-frame');
    if (frame && viewTaskId) frame.src = artifactSrc(viewTaskId);
  }

  // -- Open --
  async function openHtmlViewer(taskId) {
    const taskPane = document.querySelector('#split-modal .task-pane');
    if (!taskPane || !taskId) return;
    // Re-entrant-safe: snapshot any unsaved source edit, drop the previous
    // viewer (and its poll), then persist the snapshot before we fetch.
    let pending = null;
    if (viewTaskId || document.querySelector('.dt-htmlview')) {
      const snap = snapshot();
      destroyViewer();
      pending = persistSnapshot(snap);
    } else if (lastPersist) {
      // A just-closed viewer may still be saving - wait so we never fetch stale.
      pending = lastPersist;
    }
    const gen = ++viewGen;
    viewTaskId = taskId;
    mode = 'preview';
    pageExists = false;
    lastContent = '';
    docPath = '';

    const ov = buildOverlay(taskPane, taskId);
    taskPane.classList.add('has-editor');
    void ov.offsetWidth; // commit hidden baseline
    requestAnimationFrame(() => { if (gen === viewGen) ov.classList.add('is-open'); });

    if (pending) await pending;
    if (gen !== viewGen) return;

    let data = null, ok = false;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(taskId)}/output`);
      if (res.ok) { data = await res.json(); ok = true; }
    } catch (_) {}
    if (gen !== viewGen) return;

    docPath = (data && data.path) || '';
    ov.querySelector('.dte-doc-name').textContent = docPath ? docPath.split('/').pop() : 'page.html';
    const body = ov.querySelector('.dth-body');

    if (!ok || !data || data.format !== 'html') { showMessage(body, "Couldn't open this page."); return; }
    if (data.exists === false) { showMessage(body, "This page hasn't been written yet."); return; }

    pageExists = true;
    setKnown(taskId, data.content || '');
    mountBody(body, taskId);
    ov.querySelector('.dth-toggle').disabled = false;
    startPoll(gen);
  }

  // -- Source mode + autosave --
  async function toggleMode() {
    if (!viewTaskId || !pageExists) return;
    const frame = q('.dth-frame');
    const ta = q('.dth-source');
    const btn = q('.dth-toggle');
    const save = q('.dte-save');
    if (!frame || !ta || !btn) return;
    if (mode === 'preview') {
      mode = 'source';
      ta.value = lastContent;
      frame.hidden = true;
      ta.hidden = false;
      if (save) { save.hidden = false; setSaveState('saved'); }
      btn.textContent = 'Preview';
      btn.setAttribute('aria-pressed', 'true');
      ta.focus();
    } else {
      const gen = viewGen;
      btn.disabled = true;
      ta.readOnly = true;   // freeze input while we persist
      let saved = await settleSave();
      if (gen !== viewGen) return;
      if (saved) {
        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
        // Anything that landed after the snapshot gets one more save.
        if (ta.value !== lastContent) {
          saved = await settleSave();
          if (gen !== viewGen) return;
        }
      }
      btn.disabled = false;
      ta.readOnly = false;
      // Save failed (already toasted): stay in source so the edit isn't lost.
      if (!saved) return;
      mode = 'preview';
      ta.hidden = true;
      frame.hidden = false;
      if (save) save.hidden = true;
      btn.textContent = 'Source';
      btn.setAttribute('aria-pressed', 'false');
      reloadFrame();
    }
  }

  // Last content known to be on disk for a task (fetched, polled or saved).
  // Outlives the viewer so a close-time snapshot can tell if it still differs.
  function setKnown(taskId, content) {
    knownDisk = { taskId, content };
    if (viewTaskId === taskId) lastContent = content;
  }

  // {taskId, content} of the source textarea, or null when not editing source.
  function snapshot() {
    const ta = q('.dth-source');
    if (!viewTaskId || !ta || mode !== 'source') return null;
    return { taskId: viewTaskId, content: ta.value };
  }

  function scheduleSave() {
    setSaveState('editing');
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, SAVE_DEBOUNCE_MS);
  }

  function waitIdle() {
    return new Promise(resolve => {
      let i = 0;
      (function tick() {
        if (!saving) return resolve(true);
        if (++i > 200) return resolve(false);   // ~10s
        setTimeout(tick, 50);
      })();
    });
  }

  // The one PUT. Caller guarantees no save is in flight. Takes explicit
  // (taskId, content) so it is safe after the viewer is torn down.
  async function putContent(taskId, content) {
    const live = () => viewTaskId === taskId;
    if (live()) setSaveState('saving');
    saving = true;
    saveGen++;
    let ok = false;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(taskId)}/output`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setKnown(taskId, content);
      ok = true;
      return true;
    } catch (e) {
      if (live()) setSaveState('error');
      if (typeof toast === 'function') toast('Couldn\'t save your latest edit - please try again.');
      return false;
    } finally {
      saving = false;
      saveGen++;
      if (ok && live()) {
        // Edits typed while this PUT was in flight: queue another save.
        const ta = q('.dth-source');
        if (mode === 'source' && ta && ta.value !== lastContent) scheduleSave();
        else if (!saveTimer) setSaveState('saved');
      }
    }
  }

  // Debounced save from the live textarea. true = persisted (or nothing to
  // save), false = failed / no viewer, null = a PUT is in flight (the in-flight
  // PUT reschedules on completion, see putContent's finally).
  async function flushSave() {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    const snap = snapshot();
    if (!snap) return !!viewTaskId;
    if (saving) return null;
    if (snap.content === lastContent) { setSaveState('saved'); return true; }
    return putContent(snap.taskId, snap.content);
  }

  // Wait out any in-flight PUT, then persist the live textarea.
  async function settleSave() {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    if (!(await waitIdle())) {
      if (typeof toast === 'function') toast('Couldn\'t save - still editing source');
      return false;
    }
    const snap = snapshot();
    if (!snap) return false;
    if (snap.content === lastContent) { setSaveState('saved'); return true; }
    return putContent(snap.taskId, snap.content);
  }

  function trackPersist(p) {
    const tracked = p.catch(() => false).finally(() => { if (lastPersist === tracked) lastPersist = null; });
    lastPersist = tracked;
    return tracked;
  }

  // Persist a snapshot taken before teardown: wait out any in-flight PUT,
  // then PUT the snapshot if it still differs from what's known on disk.
  async function persistSnapshot(snap) {
    if (!snap) return true;
    if (!(await waitIdle())) {
      if (typeof toast === 'function') toast('Couldn\'t save your latest edit - please try again.');
      return false;
    }
    if (knownDisk && knownDisk.taskId === snap.taskId && knownDisk.content === snap.content) return true;
    return putContent(snap.taskId, snap.content);
  }

  // -- Live reload (chat edits the file in place) --
  function startPoll(gen) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollOnce(gen), POLL_MS);
  }

  async function pollOnce(gen) {
    if (gen !== viewGen || !viewTaskId || pollBusy || saving || document.hidden) return;
    const taskId = viewTaskId;
    const startSaveGen = saveGen;
    pollBusy = true;
    try {
      const res = await fetch(`${API}/tasks/${encodeURIComponent(taskId)}/output`);
      if (!res.ok) return;
      const data = await res.json();
      // Discard if the viewer moved on or a save started/finished meanwhile
      // (the response may predate our own write).
      if (gen !== viewGen || saving || saveGen !== startSaveGen) return;
      if (!data || data.exists === false || typeof data.content !== 'string') return;
      if (data.content === lastContent) return;
      const prev = lastContent;
      setKnown(taskId, data.content);
      if (mode === 'preview') {
        reloadFrame();
      } else {
        const ta = q('.dth-source');
        // Local wins: only replace the textarea when it holds no unsaved edits.
        if (ta && ta.value === prev && !saveTimer) ta.value = lastContent;
      }
    } catch (_) {
      // transient - next tick retries
    } finally {
      pollBusy = false;
    }
  }

  // -- Copy path --
  // toast() only surfaces errors, so success flips the button label briefly.
  let copiedTimer = null;
  function copyPath() {
    const p = docPath;
    const btn = q('.dth-copy');
    if (!p) return;
    if (!(navigator.clipboard && navigator.clipboard.writeText)) {
      if (typeof toast === 'function') toast('Couldn\'t copy the path - clipboard unavailable.');
      return;
    }
    navigator.clipboard.writeText(p)
      .then(() => {
        if (!btn || !btn.isConnected) return;
        btn.textContent = 'Copied';
        if (copiedTimer) clearTimeout(copiedTimer);
        copiedTimer = setTimeout(() => { btn.textContent = 'Copy path'; copiedTimer = null; }, 1200);
      })
      .catch(() => { if (typeof toast === 'function') toast('Couldn\'t copy the path.'); });
  }

  // -- Close / teardown --
  // The one place state is released: closeHtmlViewer's transition end, the
  // closeModal wrap, and openHtmlViewer's re-entrancy guard all land here.
  function destroyViewer() {
    viewGen++;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    viewTaskId = null;
    pageExists = false;
    mode = 'preview';
    const ov = document.querySelector('.dt-htmlview');
    if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
    const taskPane = document.querySelector('#split-modal .task-pane');
    if (taskPane && !taskPane.querySelector('.dt-editor')) taskPane.classList.remove('has-editor');
  }

  function closeHtmlViewer() {
    const ov = document.querySelector('.dt-htmlview');
    if (!ov || ov.classList.contains('is-closing')) return;
    // Snapshot the source edit now, then persist it independently of the
    // teardown (waits out any in-flight PUT first, so nothing is dropped).
    const snap = snapshot();
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    const ta = q('.dth-source');
    if (ta) ta.readOnly = true;
    trackPersist(persistSnapshot(snap));
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
    if (document.querySelector('.dt-htmlview')) {
      const snap = snapshot();
      destroyViewer();
      trackPersist(persistSnapshot(snap));
    }
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
