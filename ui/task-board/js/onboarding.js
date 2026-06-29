// --- Onboarding room ---------------------------------------------------------
// Standalone JS for the first-run concierge room (onboarding.html).
// Drives a single headless meta-onboard session over /api/onboarding/run SSE.
// On the synthetic `onboarding_complete` event it mounts the board beneath and
// runs the window-shade reveal, then redirects to /.
//
// Self-contained port of the safe chat.js rendering subset: no task coupling,
// no board globals. ASCII-only source (invariant #8). Token-only styling: the
// classes this file sets map to CSS vars declared in onboarding.html (invariant #3).
//
// XSS model (mirrored from chat.js): escape-first. Every raw character from the
// model is HTML-escaped before any tag is emitted, so the only live HTML in the
// output is the handful of tags the renderer itself generates.

(function () {
  'use strict';

  const API = '/api';
  const KICKOFF = "Hi, I'm ready to get set up.";

  let busy = false;
  let completed = false;
  let onboardingStarted = false;
  // The SSE tail replays the WHOLE accumulated transcript from the start on
  // every POST (live_runs.tail begins at index 0). This counts transcript events
  // already rendered across the page-session so each new turn shows ONLY its own
  // events - a later turn never re-stuffs an earlier turn's think/tool/text into
  // its bubble. Monotonic; a page reload resets it to 0 and rebuilds from replay.
  let renderedCount = 0;
  // Follow the streaming tail to the bottom ONLY while the reader is parked
  // there. A new turn parks the view at its own top instead (see anchorTurnTop),
  // so a long message - like the auto-fired kickoff - reads from the beginning
  // rather than being scrolled clean past its top.
  let userPinned = true;

  // -- escapeHtml (chat.js gets this from a board global; we own it here) ----
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Forcing a reflow before adding the reveal class ensures the enter-transition
  // fires even when the element was just appended (no prior paint to diff from).
  function revealNow(el, cls) { void el.offsetWidth; el.classList.add(cls); }

  // -- Markdown rendering (ported verbatim from chat.js - escape-first) ------

  function mdEscape(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function mdInline(line) {
    return line
      .split(/(`[^`]+`)/)
      .map(seg => {
        if (seg.length >= 2 && seg[0] === '`' && seg[seg.length - 1] === '`') {
          return '<code>' + mdEscape(seg.slice(1, -1)) + '</code>';
        }
        return mdFormatProse(seg);
      })
      .join('');
  }

  function mdFormatProse(seg) {
    let s = mdEscape(seg);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      if (/^(https?:|mailto:)/i.test(url)) {
        return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
      }
      return label;
    });
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
    s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^_])_([^_\s][^_]*?)_/g, '$1<em>$2</em>');
    return s;
  }

  function renderMarkdown(src) {
    const lines = String(src == null ? '' : src).split('\n');
    const out = [];
    let i = 0;
    let para = [];
    let list = null;

    const flushPara = () => {
      if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; }
    };
    const flushList = () => {
      if (list) {
        out.push('<' + list.tag + '>' + list.items.map(it => '<li>' + it + '</li>').join('') + '</' + list.tag + '>');
        list = null;
      }
    };

    while (i < lines.length) {
      const raw = lines[i];
      const fence = raw.match(/^\s*```(.*)$/);
      if (fence) {
        flushPara(); flushList();
        i += 1;
        const body = [];
        while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i += 1; }
        i += 1;
        out.push('<pre><code>' + mdEscape(body.join('\n')) + '</code></pre>');
        continue;
      }
      if (/^\s*$/.test(raw)) { flushPara(); flushList(); i += 1; continue; }
      const h = raw.match(/^\s*(#{1,3})\s+(.*)$/);
      if (h) {
        flushPara(); flushList();
        const level = h[1].length;
        out.push('<h' + level + ' class="md-h">' + mdInline(h[2]) + '</h' + level + '>');
        i += 1; continue;
      }
      const ul = raw.match(/^\s*[-*+]\s+(.*)$/);
      if (ul) {
        flushPara();
        if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
        list.items.push(mdInline(ul[1]));
        i += 1; continue;
      }
      const ol = raw.match(/^\s*\d+\.\s+(.*)$/);
      if (ol) {
        flushPara();
        if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
        list.items.push(mdInline(ol[1]));
        i += 1; continue;
      }
      if (list && list.items.length) {
        list.items[list.items.length - 1] += ' ' + mdInline(raw);
        i += 1; continue;
      }
      para.push(mdInline(raw));
      i += 1;
    }
    flushPara(); flushList();
    return out.join('');
  }

  // -- Step rendering (ported from chat.js) ----------------------------------

  const STEP_COLLAPSE_AT = 5;
  const CHEV_SVG = '<svg class="chev-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4l4 4-4 4"/></svg>';
  const CHECK_SVG = '<svg class="step-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-6.5"/></svg>';
  const NOTICE_SVG = '<svg class="notice-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.5"/><path d="M8 7.5v3.5"/><circle cx="8" cy="5" r="0.6" fill="currentColor" stroke="none"/></svg>';

  function elFromHTML(html) { const h = document.createElement('div'); h.innerHTML = html; return h.firstChild; }

  function stepHtml(s) {
    const VERB = { read: 'Read', search: 'Searched', write: 'Wrote', run: 'Ran' };
    if (s.kind === 'think') return '<div class="tool-think">' + escapeHtml(s.label) + '</div>';
    return '<div class="tool-step"><span class="tool-verb">' + (VERB[s.kind] || 'Tool') + '</span><span class="tool-target">' + escapeHtml(s.label) + '</span></div>';
  }

  function toolKind(verb) {
    const v = (verb || 'tool').toLowerCase();
    if (v.startsWith('mcp__qmd')) return 'search';
    const kindMap = { read: 'read', grep: 'search', glob: 'search', write: 'write', edit: 'write', bash: 'run' };
    return kindMap[v] || 'run';
  }

  function makeStepsGroup() {
    const group = document.createElement('div'); group.className = 'steps-group';
    const toggle = document.createElement('button'); toggle.className = 'steps-toggle'; toggle.type = 'button'; toggle.style.display = 'none';
    const list = document.createElement('div'); list.className = 'steps-list';
    const inner = document.createElement('div'); inner.className = 'steps-inner';
    list.appendChild(inner); group.appendChild(toggle); group.appendChild(list);
    return { group, toggle, list, inner };
  }

  function collapseGroup(g, count) {
    g.list.classList.add('collapsed');
    g.toggle.style.display = 'inline-flex';
    g.toggle.innerHTML = CHECK_SVG + '<span class="steps-count">Worked across ' + count + ' steps</span>' + CHEV_SVG;
    g.toggle.onclick = () => {
      const collapsed = g.list.classList.toggle('collapsed');
      g.toggle.classList.toggle('open', !collapsed);
      scrollThread();
    };
  }

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

  function renderTurn(turn, instant) {
    const el = document.createElement('div');
    el.className = 'chat-turn turn-' + turn.role + (instant ? ' show' : '');
    if (turn.role === 'user') {
      el.innerHTML = '<div class="turn-text">' + escapeHtml(turn.text || '') + '</div>';
      return el;
    }
    if (turn.role === 'error') {
      el.className = 'chat-turn turn-assistant turn-error' + (instant ? ' show' : '');
      const t = document.createElement('div');
      t.className = 'turn-text';
      t.textContent = turn.text || 'The chat run failed. You can retry.';
      el.appendChild(t);
      return el;
    }
    if (turn.role === 'notice') {
      el.className = 'chat-turn turn-notice' + (instant ? ' show' : '');
      el.innerHTML = '<div class="notice-inner">' + NOTICE_SVG + '<div class="notice-text">' + renderMarkdown(turn.text || '') + '</div></div>';
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

  function nearBottom(body) {
    return body.scrollHeight - body.scrollTop - body.clientHeight < 60;
  }

  // Chase the bottom only when the reader is already parked there. After a turn
  // is anchored to its top (userPinned = false), streaming calls here no-op, so
  // the message fills downward from its visible top; if the reader scrolls back
  // down to the bottom, following resumes.
  function scrollThread() {
    const body = document.getElementById('onboard-body');
    if (body && userPinned) body.scrollTop = body.scrollHeight;
  }

  // Park the top of a turn near the top of the viewport so a long message reads
  // from its beginning instead of having its head scrolled off. Used for each
  // new assistant turn (including the long auto-fired kickoff).
  function anchorTurnTop(el) {
    const body = document.getElementById('onboard-body');
    if (!body || !el) return;
    body.scrollTop = Math.max(0, el.offsetTop - 24);
    userPinned = false;
  }

  // -- Window-shade reveal + board redirect ----------------------------------
  function completeOnboarding() {
    if (completed) return;
    completed = true;

    const input = document.getElementById('onboard-input');
    const sendBtn = document.getElementById('onboard-send');
    if (input) input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    const banner = document.getElementById('complete-banner');
    if (banner) banner.classList.remove('hidden');

    const room = document.getElementById('onboard-room');
    const underlay = document.getElementById('board-underlay');
    let shadeStarted = false;
    // Cache-bust the board fetch: '/' served the onboarding room moments ago, and
    // a browser that cached it would otherwise re-show this very room (with its
    // "Onboard me" button) instead of the board. A unique query forces a fresh
    // fetch; the gate keys off the path, so '/?...' still resolves to the board.
    const boardUrl = '/?onboarded=' + Date.now();

    function runShade() {
      if (shadeStarted || !room) return;
      shadeStarted = true;
      // Pull the panel down 12px (.5s ease) for the "catch" sensation, then
      // lift it offscreen (2.55s slow-start cubic) revealing the board beneath.
      room.classList.add('shade-pull');
      setTimeout(() => {
        if (!room) return;
        room.classList.add('shade-lift');
        let redirected = false;
        function redirect() { if (!redirected) { redirected = true; window.location.replace(boardUrl); } }
        room.addEventListener('transitionend', e => {
          if (e.propertyName === 'transform') redirect();
        });
        // Fallback: redirect even if transitionend is dropped (interrupted transition, backgrounded tab).
        setTimeout(redirect, 3300);
      }, 650);
    }

    if (underlay) {
      // Lazy-mount the iframe: the completion marker is already set by the time
      // completeOnboarding fires, so / will serve the real board inside the iframe.
      const iframe = document.createElement('iframe');
      iframe.src = boardUrl;
      iframe.setAttribute('tabindex', '-1');
      iframe.setAttribute('aria-hidden', 'true');
      // Defensive: if the iframe load stalls beyond ~4s, run the shade anyway.
      const iframeTimer = setTimeout(runShade, 4000);
      iframe.addEventListener('load', () => {
        clearTimeout(iframeTimer);
        runShade();
      });
      underlay.appendChild(iframe);
    } else {
      setTimeout(() => { window.location.replace(boardUrl); }, 3300);
    }
  }

  // -- Main turn runner ------------------------------------------------------
  async function runTurn(text, opts) {
    const hidden = opts && opts.hidden;
    const thread = document.getElementById('onboard-thread');
    if (!thread) return;

    if (!hidden) {
      const userTurn = renderTurn({ role: 'user', text }, false);
      thread.appendChild(userTurn); revealNow(userTurn, 'show');
    }

    const a = document.createElement('div');
    a.className = 'chat-turn turn-assistant show';
    // One vertical flow; segments (steps / text) are appended in arrival order,
    // with a persistent activity indicator pinned to the bottom while streaming.
    a.innerHTML = '<div class="turn-flow"><div class="turn-activity">'
      + '<span class="typing"><span></span><span></span><span></span></span>'
      + '<span class="activity-label"></span></div></div>';
    // Park the view at the top of this new assistant turn so its message reads
    // from the start, rather than pinning to the bottom and chasing the tail.
    thread.appendChild(a); anchorTurnTop(a);
    const flow = a.querySelector('.turn-flow');
    const activity = a.querySelector('.turn-activity');
    const activityLabel = activity.querySelector('.activity-label');

    // Per-turn sequential render state. `curKind` tracks the open segment so a
    // run of consecutive think/tool events shares one steps box and a run of
    // text deltas shares one text box; when the kind flips we open a new segment
    // (appended after the previous one), preserving chronological order.
    let anyRendered = false, streamDone = false;
    let curKind = null;            // 'steps' | 'text' | null
    let stepsBox = null, liveGroup = null, toolCount = 0;   // current steps segment
    let textEl = null, rawText = '';                        // current text segment
    let connIdx = 0;               // transcript events seen on THIS connection

    // -- Liveness: a persistent "working" indicator -------------------------
    // The stream goes silent for seconds-to-minutes while Claude runs a tool or
    // thinks; without a live signal the room looks frozen (the disabled send
    // button is the only hint). Keep a labeled pulse pinned to the bottom of the
    // turn the whole time it streams: the label says what's happening, an
    // elapsed timer makes a long lull read as intentional, and the SSE heartbeat
    // (a `: ping` comment every ~15s) confirms the backend is genuinely alive.
    let activityWord = 'Getting set up';
    let activityEnded = false;
    let lastEventAt = Date.now();
    let lastBeatAt = Date.now();
    function labelForEvent(ev) {
      if (ev.kind === 'think') return 'Thinking';
      if (ev.kind === 'text') return 'Writing';
      if (ev.kind === 'tool_step') {
        const k = toolKind(ev.verb);
        return k === 'read' ? 'Reading files'
          : k === 'search' ? 'Searching'
          : k === 'write' ? 'Saving your setup'
          : 'Running a step';
      }
      return activityWord;
    }
    function refreshActivity() {
      if (activityEnded) return;
      const idleMs = Date.now() - lastEventAt;
      const idle = Math.floor(idleMs / 1000);
      // No heartbeat AND no event for a while -> the connection itself stalled.
      if (Date.now() - lastBeatAt > 22000 && idleMs > 22000) {
        activityLabel.textContent = 'Still connected - reconnecting the view...';
      } else if (idle >= 6) {
        activityLabel.textContent = activityWord + '... (' + idle + 's)';
      } else {
        activityLabel.textContent = activityWord + '...';
      }
    }
    function noteActivity(ev) {
      lastEventAt = Date.now();
      activityWord = labelForEvent(ev);
      if (activity.parentNode === flow) flow.appendChild(activity);  // keep it last
      refreshActivity();
    }
    function endActivity() {
      activityEnded = true;
      if (activityTimer) { clearInterval(activityTimer); activityTimer = null; }
      if (activity && activity.parentNode) activity.remove();
    }
    let activityTimer = setInterval(refreshActivity, 1000);
    refreshActivity();

    function startSteps() {
      if (curKind === 'steps') return;
      curKind = 'steps';
      stepsBox = document.createElement('div');
      stepsBox.className = 'turn-steps';
      flow.appendChild(stepsBox);
      liveGroup = null; toolCount = 0;
    }
    function startText() {
      if (curKind === 'text') return;
      curKind = 'text';
      textEl = document.createElement('div');
      textEl.className = 'turn-text';
      flow.appendChild(textEl);
      rawText = '';
    }

    busy = true;
    const sendBtn = document.getElementById('onboard-send');
    if (sendBtn) sendBtn.disabled = true;

    try {
      const resp = await fetch(API + '/onboarding/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      if (resp.status === 409) {
        endActivity();
        a.remove();  // drop the empty assistant shell we appended before the fetch
        const n = renderTurn({ role: 'notice', text: 'Onboarding is already running.' }, false);
        thread.appendChild(n); revealNow(n, 'show'); scrollThread();
        throw new Error('busy');
      }
      if (!resp.ok || !resp.body) {
        renderEvent({ kind: 'error', text: 'Could not reach the concierge. You can retry.' });
        throw new Error('http');
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          handleFrame(frame);
        }
        if (streamDone) break;
      }
    } catch (_) { /* message already shown on the turn */ }

    function handleFrame(frame) {
      // A `: ping` SSE comment is the backend heartbeat during a lull - no data,
      // but proof the run is alive. Use it to keep the indicator confident.
      if (frame.startsWith(':')) { lastBeatAt = Date.now(); refreshActivity(); return; }
      let isDone = false, dataLine = null;
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:') && line.slice(6).trim() === 'done') isDone = true;
        else if (line.startsWith('data:')) dataLine = line.slice(5).trim();
      }
      if (isDone) { streamDone = true; return; }
      if (!dataLine) return;
      let ev; try { ev = JSON.parse(dataLine); } catch (_) { return; }
      connIdx += 1;
      // Skip events already rendered in earlier turns (the tail replays the whole
      // transcript from 0 each POST). Only events past the page-session cursor
      // belong to THIS turn.
      if (connIdx <= renderedCount) return;
      renderedCount = connIdx;
      renderEvent(ev);
    }

    function renderEvent(ev) {
      if (ev.kind === 'think') {
        startSteps(); anyRendered = true;
        const t = elFromHTML(stepHtml({ kind: 'think', label: ev.text || '' }));
        stepsBox.appendChild(t); revealNow(t, 'in'); noteActivity(ev); scrollThread();
      } else if (ev.kind === 'tool_step') {
        startSteps(); anyRendered = true;
        if (!liveGroup) { liveGroup = makeStepsGroup(); stepsBox.appendChild(liveGroup.group); }
        const row = elFromHTML(stepHtml({ kind: toolKind(ev.verb), label: ev.target || ev.verb || '' }));
        liveGroup.inner.appendChild(row); revealNow(row, 'in'); noteActivity(ev); scrollThread();
        toolCount += 1;
        if (toolCount === STEP_COLLAPSE_AT) collapseGroup(liveGroup, toolCount);
        else if (toolCount > STEP_COLLAPSE_AT) { liveGroup.toggle.querySelector('.steps-count').textContent = 'Worked across ' + toolCount + ' steps'; }
      } else if (ev.kind === 'text') {
        startText(); anyRendered = true;
        rawText += ev.text || '';
        textEl.innerHTML = renderMarkdown(rawText);
        noteActivity(ev); scrollThread();
      } else if (ev.kind === 'error') {
        endActivity(); anyRendered = true;
        curKind = null;  // an error ends any open segment
        const er = document.createElement('div');
        er.className = 'turn-text';
        er.textContent = ev.text || 'The chat run failed. You can retry.';
        flow.appendChild(er);
        a.classList.add('turn-error');
      } else if (ev.kind === 'notice') {
        curKind = null;  // a notice is its own turn; break the segment run
        const nTurn = renderTurn({ role: 'notice', text: ev.text || '' }, false);
        thread.appendChild(nTurn); revealNow(nTurn, 'show'); scrollThread();
      } else if (ev.kind === 'onboarding_complete') {
        endActivity();
        completeOnboarding();
      }
    }

    // Stream finished: stop the working indicator. If the turn produced no new
    // events (e.g. the whole replay was already shown), drop the empty shell.
    endActivity();
    if (!anyRendered) a.remove();

    busy = false;
    if (!completed && sendBtn) sendBtn.disabled = false;
    scrollThread();
  }

  // -- sendReply -------------------------------------------------------------
  function sendReply() {
    if (busy || completed) return;
    const input = document.getElementById('onboard-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';
    runTurn(text);
  }

  // -- startOnboarding -------------------------------------------------------
  function startOnboarding() {
    if (onboardingStarted) return;
    onboardingStarted = true;
    const welcome = document.getElementById('welcome');
    const convo = document.getElementById('convo');
    if (welcome) welcome.classList.add('gone');
    if (convo) convo.classList.remove('hidden');
    const input = document.getElementById('onboard-input');
    if (input) input.focus();
    runTurn(KICKOFF, { hidden: true });
  }

  // -- Wire event listeners --------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('onboard-start');
    if (startBtn) startBtn.addEventListener('click', startOnboarding);

    const input = document.getElementById('onboard-input');
    if (input) {
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendReply(); }
      });
      input.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 140) + 'px';
      });
    }

    const sendBtn = document.getElementById('onboard-send');
    if (sendBtn) sendBtn.addEventListener('click', sendReply);

    const goBoard = document.getElementById('go-board');
    if (goBoard) goBoard.addEventListener('click', e => { e.preventDefault(); window.location = '/'; });

    // Re-pin to the bottom only when the reader scrolls (back) to the bottom;
    // any scroll up unpins, so streaming won't yank them away from what they read.
    const body = document.getElementById('onboard-body');
    if (body) body.addEventListener('scroll', () => { userPinned = nearBottom(body); });
  });

}());
