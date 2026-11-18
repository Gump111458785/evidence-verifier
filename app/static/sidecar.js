/* Trisight side panel. The conversation arrives already captured (content script ->
   background -> storage.session.liveSnapshot); a review posts it immediately. */
(() => {
  const isExtension = location.protocol === 'chrome-extension:';
  const API_BASE = isExtension ? 'https://evidence-verifier.edgeone.dev' : location.origin;
  const TOKEN_KEY = 'ev_session_token';
  const MODES = ['correct', 'supplement', 'predict'];
  const PHASES = ['review', 'search', 'verify'];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const ICON = {
    mark: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 6h9" stroke="#1c2230" stroke-width="2.2" stroke-linecap="round"/><path d="M5 15l3-5 3 5" fill="none" stroke="#1c2230" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M13 13h5m-2-2.5 2 2.5-2 2.5" fill="none" stroke="#1c2230" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    arrow: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
    check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
    cross: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    query: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/></svg>',
    link: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
    nosrc: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-dasharray="3 3" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg>',
    fill: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 10 4 15l5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>',
  };

  const state = {
    snap: null, snapKey: '', job: null, tab: 'correct', armed: false, picked: new Set(MODES), busy: false, ran: [],
    view: 'review', histRows: null, histJob: null, histRan: [], histTab: 'correct',
  };
  const histCache = new Map();
  let memToken = '';
  let toastTimer = null;

  // ------------------------------------------------------------ auth + api
  async function getToken() {
    if (memToken) return memToken;
    try {
      if (isExtension && chrome.storage?.local) {
        const res = await chrome.storage.local.get(TOKEN_KEY);
        if (res[TOKEN_KEY]) return (memToken = res[TOKEN_KEY]);
      }
      const ss = globalThis.sessionStorage?.getItem(TOKEN_KEY) || '';
      if (/^[a-f0-9]{64}$/.test(ss)) return (memToken = ss);
    } catch (_) {}
    return '';
  }
  async function saveToken(tok) {
    memToken = tok || '';
    try {
      if (isExtension && chrome.storage?.local) await (tok ? chrome.storage.local.set({ [TOKEN_KEY]: tok }) : chrome.storage.local.remove(TOKEN_KEY));
      if (globalThis.sessionStorage) tok ? sessionStorage.setItem(TOKEN_KEY, tok) : sessionStorage.removeItem(TOKEN_KEY);
    } catch (_) {}
  }
  async function api(path, options = {}) {
    const tok = await getToken();
    const headers = {};
    if (tok) headers.Authorization = 'Bearer ' + tok;
    if (options.body) headers['Content-Type'] = 'application/json';
    const method = (options.method || 'GET').toUpperCase();
    const maxAttempts = 3;
    let lastErr = null;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        const res = await fetch(API_BASE + path, {
          method, headers,
          credentials: isExtension ? 'omit' : 'same-origin',
          body: options.body ? JSON.stringify(options.body) : undefined,
          signal: AbortSignal.timeout(options.timeoutMs || 60000),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const transient5xx = !data.code && [500, 502, 503, 504].includes(res.status);
          if (transient5xx && attempt < maxAttempts - 1) {
            await new Promise((r) => setTimeout(r, 350 * (attempt + 1)));
            continue;
          }
          const e = new Error(data.error || (res.status >= 500 ? '云端服务暂时繁忙（' + res.status + '），请再试一次' : '请求失败（' + res.status + '）'));
          e.status = res.status; e.code = data.code;
          throw e;
        }
        return data;
      } catch (err) {
        if (err && typeof err.status === 'number') throw err;
        lastErr = err;
        if (attempt < maxAttempts - 1) {
          await new Promise((r) => setTimeout(r, 300 * (attempt + 1)));
          continue;
        }
      }
    }
    const raw = String((lastErr && lastErr.message) || '');
    const msg = /failed to fetch|network|load failed|abort/i.test(raw)
      ? '网络连接暂时中断，请重试'
      : (raw || '网络请求失败');
    throw new Error(msg);
  }
  async function pageCmd(mode, prompt) {
    const r = await chrome.runtime.sendMessage({ type: 'ev-page-command', mode, prompt });
    if (!r || !r.ok) throw new Error(r?.error || '页面没有响应');
    return r.value;
  }

  // ------------------------------------------------------------ small ui helpers
  function toast(text) {
    const t = $('toast');
    t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 1800);
  }
  function alertMsg(text) { $('alert').textContent = text || ''; $('alert').hidden = !text; }
  const fields = () => ({ question: $('question').value.trim(), answer: $('candidate').value.trim() });

  function syncButtons() {
    const { question, answer } = fields();
    const ready = Boolean(question && answer) && !state.busy;
    $('run-auto').disabled = !ready;
    $('run-manual').disabled = !ready || (state.armed && state.picked.size === 0);
  }

  // ------------------------------------------------------------ captured conversation
  function apply(snap) {
    if (!snap || typeof snap !== 'object') return;
    const src = $('src');
    src.hidden = false;
    $('src-name').textContent = snap.platform || '—';
    src.title = snap.error || '查看读取到的问答';
    if (snap.idle || (!snap.platform && !snap.question)) src.dataset.state = 'idle';
    else if (snap.streaming) src.dataset.state = 'live';
    else if (snap.error) src.dataset.state = 'error';
    else src.dataset.state = 'ready';
    if (!(snap.question && snap.candidate)) { syncButtons(); return; }
    const key = JSON.stringify([snap.question, snap.candidate]);
    if (key !== state.snapKey) {
      state.snapKey = key;
      state.snap = snap;
      $('question').value = snap.question;
      $('candidate').value = snap.candidate;
      const turns = snap.context || [];
      const n = turns.length;
      const ctxLabel = n ? '查看上文 ' + n + ' 段' + (snap.context_dropped ? '（另有 ' + snap.context_dropped + ' 段超出未带）' : '') : '';
      $('ctx').textContent = n ? '+ ' + ctxLabel : '';
      if ($('ctx-status')) $('ctx-status').textContent = ctxLabel || '暂无更早上下文';
      if ($('toggle-ctx')) $('toggle-ctx').disabled = n === 0;
      if ($('ctx-list')) {
        $('ctx-list').innerHTML = n
          ? turns.map((t) => '<div class="ctx-turn"><b>' + (t.role === 'user' ? '问' : '答') + '</b><span>' + esc(t.text) + '</span></div>').join('')
          : '<div class="ctx">暂无更早上下文</div>';
      }
      if (n === 0) toggleCtxDrawer(false);
      if (state.job) reset();
    }
    syncButtons();
  }

  async function attach() {
    if (!isExtension) {
      $('src').hidden = false; $('src').dataset.state = 'ready'; $('src-name').textContent = '手动';
      toggleDrawer(true);
      syncButtons();
      return;
    }
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'session' && changes.liveSnapshot?.newValue) apply(changes.liveSnapshot.newValue);
    });
    const stored = await chrome.storage.session.get('liveSnapshot');
    if (stored?.liveSnapshot) apply(stored.liveSnapshot);
    if (!stored?.liveSnapshot?.question) {
      try { apply(await pageCmd('capture')); } catch (e) { apply({ idle: true }); }
    }
  }

  // ------------------------------------------------------------ results
  function reset() {
    state.job = null; state.ran = [];
    document.querySelectorAll('.tab').forEach((b) => { b.disabled = true; });
    MODES.forEach((m) => { $('n-' + m).hidden = true; });
    $('meter').hidden = true;
    const pane = $('pane');
    pane.className = 'pane empty';
    pane.innerHTML = ICON.mark;
  }

  function meter(phase, done) {
    const at = PHASES.indexOf(phase);
    $('meter').hidden = false;
    $('meter').querySelectorAll('i').forEach((seg, i) => { seg.dataset.s = done || i < at ? 'done' : i === at ? 'on' : ''; });
  }

  function counts(r, prefix = 'n-') {
    const m = r.modes;
    const set = (mode, n, cls) => { const c = $(prefix + mode); if (!c) return; c.hidden = n === null; c.textContent = n === null ? '' : String(n); c.className = 'count' + (cls ? ' ' + cls : ''); };
    const wrong = (m.correct.claims || []).filter((c) => c.verdict === 'contradicted').length;
    if (wrong) set('correct', wrong, 'err');
    else if (m.correct.verdict === 'no_error') { set('correct', '✓', 'ok'); }
    else set('correct', null);
    const pts = (m.supplement.points || []).length; set('supplement', pts || null, 'sup');
    const qs = (m.predict.questions || []).length; set('predict', qs || null, '');
  }

  function firstTab(r, ran = state.ran) {
    const m = r.modes;
    if (ran.includes('correct') && (m.correct.claims || []).length) return 'correct';
    if (ran.includes('supplement') && (m.supplement.points || []).length) return 'supplement';
    if (ran.includes('predict') && (m.predict.questions || []).length) return 'predict';
    return ran[0] || 'correct';
  }

  function quote(q) {
    if (!q || !q.text) return '';
    const where = q.url ? ' <a href="' + esc(q.url) + '" target="_blank" rel="noopener" title="' + esc(q.title || q.url) + '">' + ICON.link + '</a>' : '';
    return '<div class="q">“' + esc(q.text) + '”' + where + '</div>';
  }

  const fillBtn = (text) => (text ? '<button type="button" class="fill" data-fill="' + esc(text) + '">' + ICON.fill + (isExtension ? '填入对话' : '复制提示词') + '</button>' : '');

  function renderPaneInto(pane, r, tab) {
    if (!r) { pane.className = 'pane empty'; pane.innerHTML = ICON.mark; return; }
    pane.className = 'pane';
    if (r.status !== 'ok') { pane.innerHTML = '<div class="head err">' + ICON.cross + esc(r.error || '本次结果无效') + '</div>'; return; }
    const m = r.modes;
    let html = '';
    if (tab === 'correct') {
      const claims = m.correct.claims || [];
      const wrong = claims.filter((c) => c.verdict === 'contradicted').length;
      const unsure = claims.filter((c) => c.verdict === 'insufficient' || c.verdict === 'invalid_output').length;
      if (wrong) html += '<div class="head err">' + ICON.cross + wrong + ' 处有误</div>';
      else if (m.correct.verdict === 'no_error') html += '<div class="head ok">' + ICON.check + '未发现错误</div>';
      else if (unsure) html += '<div class="head warn">' + ICON.query + unsure + ' 处未能核实</div>';
      else html += '<div class="head mute">无可核实的事实</div>';
      const order = { contradicted: 0, insufficient: 1, invalid_output: 1, supported: 2 };
      for (const c of claims.slice().sort((a, b) => order[a.verdict] - order[b.verdict])) {
        if (c.verdict === 'contradicted') {
          html += '<div class="item contradicted"><del>' + esc(c.text) + '</del><div class="fix">' + ICON.arrow + '<span>' + esc(c.correction) + '</span></div>' + quote(c.quote) + '</div>';
        } else if (c.verdict === 'supported') {
          html += '<div class="item supported">' + esc(c.text) + quote(c.quote) + '</div>';
        } else {
          html += '<div class="item ' + c.verdict + '">' + esc(c.text) + '<div class="why">' + esc(c.explanation) + '</div></div>';
        }
      }
      html += fillBtn(r.prompts?.correct);
    } else if (tab === 'supplement') {
      const pts = m.supplement.points || [];
      if (!pts.length) html += '<div class="head mute">' + ICON.check + '无需补充</div>';
      const mark = { sourced: ['sourced', ICON.link, '有来源'], unsourced: ['unsourced', ICON.nosrc, '未找到来源'] };
      for (const p of pts) {
        const s = mark[p.source_status];
        html += '<div class="item supplement"><b>' + esc(p.point) + '</b>' +
          (s ? '<span class="src-mark ' + s[0] + '" title="' + s[2] + '">' + s[1] + '</span>' : '') +
          '<div>' + esc(p.content) + '</div>' + quote(p.quote) + '</div>';
      }
      html += fillBtn(r.prompts?.supplement);
    } else {
      const qs = m.predict.questions || [];
      if (!qs.length) html += '<div class="head mute">—</div>';
      for (const q of qs) html += '<button type="button" class="nq" data-fill="' + esc(q.question) + '"><span>' + esc(q.question) + '</span>' + ICON.fill + '</button>';
    }
    pane.innerHTML = html;
    pane.querySelectorAll('[data-fill]').forEach((b) => b.addEventListener('click', () => fill(b.dataset.fill)));
  }

  function renderTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach((b) => b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false'));
    renderPaneInto($('pane'), state.job?.result, tab);
  }

  function renderHistTab(tab) {
    state.histTab = tab;
    document.querySelectorAll('.htab').forEach((b) => b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false'));
    renderPaneInto($('hpane'), state.histJob?.result, tab);
  }

  async function fill(text) {
    if (isExtension) {
      try { await pageCmd('insert', text); toast('已填入'); return; }
      catch (e) {
        try { await navigator.clipboard.writeText(text); toast(e.message + '，已复制'); } catch (_) { toast(e.message); }
        return;
      }
    }
    try { await navigator.clipboard.writeText(text); toast('已复制'); } catch (_) { toast('复制失败'); }
  }

  // ------------------------------------------------------------ run
  async function run(modes) {
    const { question, answer } = fields();
    if (state.busy || !question || !answer || !modes.length) return;
    alertMsg('');
    state.busy = true; syncButtons();
    reset();
    state.ran = modes.slice();
    const pane = $('pane'); pane.className = 'pane empty'; pane.innerHTML = '';
    meter('review');
    const snapUsed = state.snap && state.snap.question === question && state.snap.candidate === answer;
    const body = { question, answer, modes, compose: false, client: isExtension ? 'extension' : 'web' };
    if (snapUsed && (state.snap.context || []).length) body.context = state.snap.context;
    try {
      let job = await api('/api/assist', { method: 'POST', body });
      let polls = 0;
      while (job.state === 'queued' || (job.state === 'running' && polls < 8)) {
        meter((job.progress || {}).phase || 'review');
        if (job.state === 'running') {
          polls += 1;
          await new Promise((r) => setTimeout(r, 1500));
          job = await api('/api/assist/' + job.id);
        } else {
          job = await api(job.advance_path, { method: 'POST' });
        }
      }
      if (job.state !== 'completed') throw new Error(job.error || '审查未完成');
      state.job = job;
      state.histRows = null;
      $('meter').hidden = true;
      document.querySelectorAll('.tab').forEach((b) => { b.disabled = !state.ran.includes(b.dataset.tab); });
      if (job.result.status === 'ok') counts(job.result);
      renderTab(firstTab(job.result));
    } catch (e) {
      $('meter').hidden = true;
      renderTab(state.tab);
      if (e.status === 401) { await saveToken(''); showLogin(); return; }
      alertMsg(e.message);
    } finally {
      state.busy = false; syncButtons();
    }
  }

  function arm(on) {
    state.armed = on;
    $('chips').hidden = !on;
    $('run-manual').classList.toggle('armed', on);
    $('run-manual').parentElement.classList.toggle('manual', on);
    $('run-manual').setAttribute('aria-expanded', on ? 'true' : 'false');
    syncButtons();
  }

  // ------------------------------------------------------------ history
  function fmtTime(sec) {
    if (!sec) return '';
    const d = new Date(sec * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function histPill(r) {
    if (r.state !== 'completed') return '<span class="hist-pill">未完成</span>';
    if (r.status !== 'ok') return '<span class="hist-pill">结果无效</span>';
    const v = r.correct || 'not_run';
    if (v === 'errors_found') return '<span class="hist-pill err">发现错误</span>';
    if (v === 'no_error') return '<span class="hist-pill ok">未发现错误</span>';
    if (v === 'unverified') return '<span class="hist-pill warn">部分未能核实</span>';
    if (v === 'no_checkable_claims') return '<span class="hist-pill">无核实断言</span>';
    return '<span class="hist-pill">未纠错</span>';
  }

  function renderHistList(rows) {
    const list = $('hist-list');
    if (!rows.length) {
      list.className = 'pane empty';
      list.innerHTML = '<div class="head mute">暂无审查历史记录</div>';
      return;
    }
    list.className = 'pane hist-list';
    list.innerHTML = rows.map((r) => {
      const extra = [];
      if (r.supplement) extra.push('补充 ' + r.supplement);
      if (r.predict) extra.push('预测 ' + r.predict);
      return '<button type="button" class="hist-card" data-id="' + esc(r.id) + '">' +
        '<div class="hist-q">' + esc(r.question || '未命名问答') + '</div>' +
        '<div class="hist-meta-row">' + histPill(r) +
          (extra.length ? '<span>' + esc(extra.join(' · ')) + '</span>' : '') +
          '<span class="hist-time">' + esc(fmtTime(r.created)) + '</span>' +
        '</div>' +
      '</button>';
    }).join('');
    list.querySelectorAll('.hist-card').forEach((b) => b.addEventListener('click', () => openHistory(b.dataset.id)));
  }

  async function loadHistory(force = false) {
    $('hist-list-wrap').hidden = false;
    $('hist-detail-wrap').hidden = true;
    if (!force && Array.isArray(state.histRows)) {
      renderHistList(state.histRows);
      return;
    }
    const list = $('hist-list');
    list.className = 'pane empty';
    list.innerHTML = '<div class="head mute">正在载入历史记录…</div>';
    try {
      const d = await api('/api/assist?limit=30');
      state.histRows = d.rows || [];
      if ($('hist-meta')) $('hist-meta').textContent = '共 ' + (d.total ?? state.histRows.length) + ' 条审查记录';
      renderHistList(state.histRows);
    } catch (e) {
      if (e.status === 401) { await saveToken(''); showLogin(); return; }
      list.className = 'pane';
      list.innerHTML = '<div class="head err">' + ICON.cross + esc(e.message) + '</div>';
    }
  }

  async function openHistory(id) {
    $('hist-list-wrap').hidden = true;
    $('hist-detail-wrap').hidden = false;
    const hpane = $('hpane');
    hpane.className = 'pane empty';
    hpane.innerHTML = '<div class="head mute">正在载入详情…</div>';
    MODES.forEach((m) => { const el = $('hn-' + m); if (el) el.hidden = true; });
    try {
      let job = histCache.get(id);
      if (!job) {
        job = await api('/api/assist/' + id);
        histCache.set(id, job);
      }
      state.histJob = job;
      if ($('hist-time')) $('hist-time').textContent = fmtTime(job.created);
      if ($('hist-qa-body')) {
        $('hist-qa-body').innerHTML = '<div><b>问：</b>' + esc(job.input?.question || '') + '</div><div><b>答：</b>' + esc(job.input?.answer || '') + '</div>';
      }
      if (job.state !== 'completed' || !job.result) {
        document.querySelectorAll('.htab').forEach((b) => { b.disabled = true; });
        hpane.className = 'pane';
        hpane.innerHTML = '<div class="head warn">' + ICON.query + esc(job.error || '该次审查未完成') + '</div>';
        return;
      }
      const reqModes = job.input?.modes === 'auto' || !Array.isArray(job.input?.modes) ? MODES : job.input.modes;
      state.histRan = reqModes;
      document.querySelectorAll('.htab').forEach((b) => { b.disabled = !reqModes.includes(b.dataset.tab); });
      if (job.result.status === 'ok') counts(job.result, 'hn-');
      renderHistTab(firstTab(job.result, reqModes));
    } catch (e) {
      if (e.status === 401) { await saveToken(''); showLogin(); return; }
      hpane.className = 'pane';
      hpane.innerHTML = '<div class="head err">' + ICON.cross + esc(e.message) + '</div>';
    }
  }

  // ------------------------------------------------------------ views
  function switchView(view) {
    state.view = view;
    if ($('nav-review')) $('nav-review').setAttribute('aria-selected', view === 'review' ? 'true' : 'false');
    if ($('nav-history')) $('nav-history').setAttribute('aria-selected', view === 'history' ? 'true' : 'false');
    $('work').hidden = view !== 'review';
    $('history').hidden = view !== 'history';
    if (view === 'history') {
      toggleDrawer(false);
      toggleCtxDrawer(false);
      loadHistory();
    }
  }

  function showLogin() { $('views').hidden = true; $('work').hidden = true; $('history').hidden = true; $('drawer').hidden = true; if ($('ctx-drawer')) $('ctx-drawer').hidden = true; $('src').hidden = true; $('logout').hidden = true; $('login').hidden = false; }
  async function showWork() { $('login').hidden = true; $('views').hidden = false; $('logout').hidden = false; switchView('review'); reset(); await attach(); }

  function toggleDrawer(forceOpen) {
    const open = typeof forceOpen === 'boolean' ? forceOpen : $('drawer').hidden;
    if (open) toggleCtxDrawer(false);
    $('drawer').hidden = !open;
    $('src').setAttribute('aria-expanded', open ? 'true' : 'false');
    if ($('toggle-qa')) {
      $('toggle-qa').setAttribute('aria-expanded', open ? 'true' : 'false');
      $('toggle-qa').textContent = open ? '收起 QA' : '查看 QA';
    }
  }

  function toggleCtxDrawer(forceOpen) {
    const box = $('ctx-drawer');
    if (!box) return;
    const open = typeof forceOpen === 'boolean' ? forceOpen : box.hidden;
    if (open) toggleDrawer(false);
    box.hidden = !open;
    if ($('toggle-ctx')) $('toggle-ctx').setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function bind() {
    $('src').addEventListener('click', () => toggleDrawer());
    if ($('toggle-qa')) $('toggle-qa').addEventListener('click', () => toggleDrawer());
    if ($('toggle-ctx')) $('toggle-ctx').addEventListener('click', () => toggleCtxDrawer());
    $('question').addEventListener('input', syncButtons);
    $('candidate').addEventListener('input', syncButtons);
    $('run-auto').addEventListener('click', () => { arm(false); run(MODES); });
    $('run-manual').addEventListener('click', () => {
      if (!state.armed) { arm(true); return; }
      run(MODES.filter((m) => state.picked.has(m)));
    });
    $('pull-ctx').addEventListener('click', async () => {
      if (!isExtension) {
        toggleDrawer(true);
        $('question').focus();
        return;
      }
      const btn = $('pull-ctx');
      btn.disabled = true; alertMsg('');
      try {
        const snap = await pageCmd('capture');
        apply(snap);
        const n = (snap.context || []).length;
        if (n > 0) toggleCtxDrawer(true);
        toast(n ? '已获取上文 ' + n + ' 段' : '当前为首轮，暂无更早上下文');
      } catch (err) {
        alertMsg(err.message);
      } finally {
        btn.disabled = false;
      }
    });
    document.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => {
      const m = c.dataset.mode;
      state.picked.has(m) ? state.picked.delete(m) : state.picked.add(m);
      c.setAttribute('aria-pressed', state.picked.has(m) ? 'true' : 'false');
      syncButtons();
    }));
    document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) renderTab(b.dataset.tab); }));
    document.querySelectorAll('.htab').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) renderHistTab(b.dataset.tab); }));
    document.querySelectorAll('.nav-btn').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));
    if ($('hist-refresh')) $('hist-refresh').addEventListener('click', () => loadHistory(true));
    if ($('hist-back')) $('hist-back').addEventListener('click', () => { $('hist-detail-wrap').hidden = true; $('hist-list-wrap').hidden = false; });
    if ($('hist-load')) $('hist-load').addEventListener('click', () => {
      const j = state.histJob;
      if (!j || !j.input) return;
      $('question').value = j.input.question || '';
      $('candidate').value = j.input.answer || '';
      state.snapKey = JSON.stringify([$('question').value, $('candidate').value]);
      state.snap = { platform: state.snap?.platform || '历史记录', question: $('question').value, candidate: $('candidate').value, context: j.input.context || [] };
      reset();
      switchView('review');
      toggleDrawer(true);
      syncButtons();
    });
    $('login').addEventListener('submit', async (e) => {
      e.preventDefault();
      $('login-err').hidden = true; $('login-btn').disabled = true;
      try {
        const res = await api('/api/auth/login', { method: 'POST', body: { identifier: $('login-id').value.trim(), password: $('login-pw').value } });
        if (!res.authenticated) throw new Error('登录失败');
        if (res.session_token) await saveToken(res.session_token);
        await showWork();
      } catch (err) { $('login-err').textContent = err.message; $('login-err').hidden = false; }
      finally { $('login-btn').disabled = false; }
    });
    $('logout').addEventListener('click', async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
      await saveToken(''); showLogin();
    });
  }

  async function init() {
    bind();
    try { const me = await api('/api/auth/me'); me && me.authenticated ? await showWork() : showLogin(); }
    catch (_) { showLogin(); }
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { run, apply, renderTab, renderHistTab, switchView, loadHistory, openHistory, init, state, arm };
  init();
})();
