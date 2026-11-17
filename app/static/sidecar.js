(() => {
  const isExtension = location.protocol === 'chrome-extension:';
  const API_BASE = isExtension ? 'https://evidence-verifier.edgeone.dev' : location.origin;
  const TOKEN_KEY = 'ev_session_token';
  const $ = (id) => document.getElementById(id);

  const VERDICT = {
    errors_found: ['err', '发现错误'],
    no_error: ['ok', '未发现错误'],
    unverified: ['warn', '部分未能核实'],
    no_checkable_claims: ['neutral', '无核实断言'],
    not_run: ['neutral', '未运行'],
  };
  const CLAIM = {
    contradicted: '有误',
    supported: '有证据支持',
    insufficient: '未能核实',
    invalid_output: '结果无效',
  };
  const STAGES = [
    { key: 'review', num: '1', name: '初审' },
    { key: 'search', num: '2', name: '检索' },
    { key: 'verify', num: '3', name: '核验' },
    { key: 'compose', num: '4', name: '修订' },
  ];

  let activeTab = 'correct';
  let currentJob = null;
  let busy = false;
  let danmakuTimer = null;
  let memToken = '';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function danmaku(text, state = 'running', hideMs = 2000) {
    let stage = document.querySelector('.sc-danmaku-stage');
    if (!stage) {
      stage = document.createElement('div');
      stage.className = 'sc-danmaku-stage';
      stage.innerHTML = '<div id="sc-danmaku" class="sc-danmaku" role="status" hidden><span id="sc-danmaku-text"></span></div>';
      document.body.appendChild(stage);
    }
    const box = $('sc-danmaku'), label = $('sc-danmaku-text');
    if (!box || !label) return;
    if (danmakuTimer) { clearTimeout(danmakuTimer); danmakuTimer = null; }
    label.textContent = text;
    box.dataset.state = state;
    box.hidden = false;
    box.classList.remove('enter');
    void box.offsetWidth;
    box.classList.add('enter');
    if (hideMs > 0) danmakuTimer = setTimeout(() => { box.hidden = true; }, hideMs);
  }

  async function getToken() {
    if (memToken) return memToken;
    try {
      if (isExtension && chrome.storage?.local) {
        const res = await chrome.storage.local.get(TOKEN_KEY);
        if (res[TOKEN_KEY]) { memToken = res[TOKEN_KEY]; return memToken; }
      }
      const ss = globalThis.sessionStorage?.getItem(TOKEN_KEY) || '';
      if (/^[a-f0-9]{64}$/.test(ss)) { memToken = ss; return ss; }
    } catch (_) {}
    return '';
  }

  async function saveToken(tok) {
    memToken = tok || '';
    try {
      if (isExtension && chrome.storage?.local) {
        if (tok) await chrome.storage.local.set({ [TOKEN_KEY]: tok });
        else await chrome.storage.local.remove(TOKEN_KEY);
      }
      if (globalThis.sessionStorage) {
        if (tok) sessionStorage.setItem(TOKEN_KEY, tok);
        else sessionStorage.removeItem(TOKEN_KEY);
      }
    } catch (_) {}
  }

  async function api(path, options = {}) {
    const tok = await getToken();
    const hdr = {};
    if (tok) hdr.Authorization = 'Bearer ' + tok;
    if (options.body) hdr['Content-Type'] = 'application/json';
    const res = await fetch(API_BASE + path, {
      method: options.method || 'GET',
      credentials: isExtension ? 'omit' : 'same-origin',
      headers: hdr,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: AbortSignal.timeout(options.timeoutMs || 45000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `请求失败（${res.status}）`);
      err.status = res.status;
      err.code = data.code;
      throw err;
    }
    return data;
  }

  async function pageCmd(mode, prompt) {
    if (!isExtension) throw new Error('浏览器网页预览无法跨标签页插入，已为您复制');
    const r = await chrome.runtime.sendMessage({ type: 'ev-page-command', mode, prompt });
    if (!r || !r.ok) throw new Error(r?.error || '读取对话失败');
    return r.value;
  }

  function setSyncState(ready, label) {
    const pill = $('sc-sync'), text = $('sc-sync-text');
    if (!pill || !text) return;
    pill.classList.toggle('ready', Boolean(ready));
    text.textContent = label;
  }

  function applySnapshot(snap) {
    if (!snap || typeof snap !== 'object') return;
    $('question').value = snap.question || '';
    $('candidate').value = snap.candidate || '';
    const ready = Boolean(snap.question && snap.candidate);
    setSyncState(ready, ready ? `${snap.platform || '对话'} · 已同步` : '等待对话');
  }

  async function autoAttachConversation() {
    if (!isExtension) {
      $('sc-edit-toggle').hidden = false;
      setSyncState(false, '可编辑输入');
      return;
    }
    try {
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'session' && changes.liveSnapshot?.newValue) {
          applySnapshot(changes.liveSnapshot.newValue);
        }
      });
      const stored = await chrome.storage.session.get('liveSnapshot');
      if (stored?.liveSnapshot) applySnapshot(stored.liveSnapshot);
      const fresh = await pageCmd('watch');
      applySnapshot(fresh);
    } catch (_) {
      setSyncState(false, '等待 AI 对话页');
    }
  }

  function renderTimeline(activePhase, doneSet) {
    const idx = STAGES.findIndex((s) => s.key === activePhase);
    $('sc-pane').innerHTML = '<div class="sc-tl">' + STAGES.map((st, i) => {
      const s = doneSet.has(st.key) || i < idx ? 'done' : st.key === activePhase ? 'active' : 'wait';
      return `<div class="sc-tl-step" data-s="${s}"><span class="sc-tl-dot">${s === 'done' ? '✓' : st.num}</span><span class="sc-tl-name">${st.name}</span></div>`;
    }).join('') + '</div>';
  }

  function updateBadges(result) {
    const m = result?.modes;
    if (!m) return;
    const bc = $('b-correct'), bs = $('b-supplement'), bp = $('b-predict');
    const cErr = (m.correct.claims || []).filter((x) => x.verdict === 'contradicted').length;
    if (cErr > 0) { bc.hidden = false; bc.textContent = String(cErr); bc.className = 'sc-badge err'; }
    else if (m.correct.verdict === 'no_error') { bc.hidden = false; bc.textContent = '✓'; bc.className = 'sc-badge ok'; }
    else { bc.hidden = true; }

    const sLen = (m.supplement.points || []).length;
    if (sLen > 0) { bs.hidden = false; bs.textContent = String(sLen); bs.className = 'sc-badge sup'; }
    else { bs.hidden = true; }

    const pLen = (m.predict.questions || []).length;
    if (pLen > 0) { bp.hidden = false; bp.textContent = String(pLen); bp.className = 'sc-badge'; }
    else { bp.hidden = true; }
  }

  async function fillOrCopy(text) {
    if (!text) return;
    if (isExtension) {
      try {
        await pageCmd('insert', text);
        danmaku('已填入原对话草稿', 'done', 2000);
        return;
      } catch (e) {
        try { await navigator.clipboard.writeText(text); } catch (_) {}
        danmaku(e.message + '（已复制）', 'done', 2400);
        return;
      }
    }
    try {
      await navigator.clipboard.writeText(text);
      danmaku('已复制到剪贴板', 'done', 2000);
    } catch (_) {
      danmaku('复制失败', 'error', 2000);
    }
  }

  function renderTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.sc-tab').forEach((b) => {
      b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false');
    });
    const pane = $('sc-pane');
    if (!currentJob || !currentJob.result) {
      pane.innerHTML = '<div class="sc-empty">选择方式并点击「开始审阅」</div>';
      return;
    }
    const r = currentJob.result;
    if (r.status !== 'ok') {
      pane.innerHTML = `<div class="sc-err">${esc(r.error || r.status)}</div>`;
      return;
    }
    const m = r.modes;
    const actionLabel = isExtension ? '填入原对话草稿' : '复制';

    if (tab === 'correct') {
      const [cls, label] = VERDICT[m.correct.verdict] || ['neutral', m.correct.verdict];
      let html = `<div class="sc-verdict-row"><span class="sc-verdict-pill ${cls}">${label}</span></div>`;
      for (const cl of m.correct.claims || []) {
        const q = cl.quote;
        const qHtml = q && q.text ? `<blockquote class="sc-quote">“${esc(q.text)}”${q.url ? ` · <a href="${esc(q.url)}" target="_blank" rel="noopener">${esc(q.title || '来源')}</a>` : ''}</blockquote>` : '';
        html += `<div class="sc-item ${cl.verdict}"><span class="sc-item-tag">${CLAIM[cl.verdict] || cl.verdict}</span>“${esc(cl.text)}”` +
          (cl.correction ? `<div class="sc-fix">更正：${esc(cl.correction)}</div>` : '') + qHtml + `</div>`;
      }
      if (!m.correct.claims?.length && m.correct.verdict !== 'not_run') {
        html += `<div class="sc-empty" style="padding:14px 0">未检测到需纠偏的事实断言</div>`;
      }
      if (r.prompts?.correct) {
        html += `<div class="sc-act-row"><button type="button" class="sc-act-btn primary" data-fill="${esc(r.prompts.correct)}">${actionLabel}纠错提示</button></div>`;
      }
      pane.innerHTML = html;
    } else if (tab === 'supplement') {
      const pts = m.supplement.points || [];
      let html = `<div class="sc-verdict-row"><span class="sc-verdict-pill ${pts.length ? 'sup' : 'neutral'}">${pts.length ? pts.length + ' 项建议补充' : '无需补充'}</span></div>`;
      for (const p of pts) {
        const q = p.quote;
        const qHtml = q && q.text ? `<blockquote class="sc-quote">“${esc(q.text)}”</blockquote>` : '';
        html += `<div class="sc-item supplement"><b>${esc(p.point)}</b>：${esc(p.content)}${qHtml}</div>`;
      }
      if (r.prompts?.supplement) {
        html += `<div class="sc-act-row"><button type="button" class="sc-act-btn primary" data-fill="${esc(r.prompts.supplement)}">${actionLabel}补充提示</button></div>`;
      }
      pane.innerHTML = html;
    } else if (tab === 'predict') {
      const qs = m.predict.questions || [];
      let html = `<div class="sc-verdict-row"><span class="sc-verdict-pill neutral">${qs.length ? '点击问题直接填入' : '暂无追问建议'}</span></div>`;
      for (const q of qs) {
        html += `<button type="button" class="sc-qbtn" data-fill="${esc(q.question)}">${esc(q.question)}</button>`;
      }
      pane.innerHTML = html;
    } else if (tab === 'answer') {
      if (r.answer?.status === 'ok') {
        pane.innerHTML = `<div class="sc-revised">${esc(r.answer.revised_answer)}</div>` +
          `<div class="sc-act-row"><button type="button" class="sc-act-btn primary" data-fill="${esc(r.answer.revised_answer)}">${actionLabel}修订稿</button></div>`;
      } else {
        pane.innerHTML = `<div class="sc-empty">${r.answer ? esc(r.answer.error) : '原回答无需整体修订'}</div>`;
      }
    }

    pane.querySelectorAll('[data-fill]').forEach((btn) => {
      btn.addEventListener('click', () => fillOrCopy(btn.dataset.fill));
    });
  }

  function pickDefaultTab(result) {
    const m = result?.modes;
    if (!m) return 'correct';
    if ((m.correct.claims || []).some((x) => x.verdict === 'contradicted')) return 'correct';
    if (m.correct.needed) return 'correct';
    if (m.supplement.needed && (m.supplement.points || []).length) return 'supplement';
    if (m.predict.needed && (m.predict.questions || []).length) return 'predict';
    return 'correct';
  }

  async function startReview() {
    if (busy) return;
    $('sc-error').hidden = true;

    // In extension mode, ensure latest Q&A from active tab before submitting
    if (isExtension) {
      try {
        const snap = await pageCmd('capture');
        applySnapshot(snap);
      } catch (_) {}
    }

    const question = $('question').value.trim();
    const candidate = $('candidate').value.trim();
    if (!question || !candidate) {
      if (!isExtension) $('sc-ctx-drawer').hidden = false;
      $('sc-error').textContent = isExtension ? '未捕获到当前对话问答，请在 ChatGPT / Claude / Gemini 回答页面试。' : '请填写问题与待审回答。';
      $('sc-error').hidden = false;
      return;
    }

    const how = document.querySelector('input[name="sc-how"]:checked')?.value || 'auto';
    let modes;
    if (how === 'auto') {
      // Auto mode runs all three modes unconditionally
      modes = ['correct', 'supplement', 'predict'];
    } else {
      modes = [...document.querySelectorAll('input[name="sc-mode"]:checked')].map((c) => c.value);
      if (!modes.length) {
        $('sc-error').textContent = '请至少选择一种审查模式。';
        $('sc-error').hidden = false;
        return;
      }
    }

    busy = true;
    $('sc-run').disabled = true;
    danmaku('正在审阅…', 'running', 1800);
    const done = new Set();
    renderTimeline('review', done);

    try {
      let job = await api('/api/assist', {
        method: 'POST',
        body: { question, answer: candidate, modes, compose: true, client: isExtension ? 'extension' : 'web' },
      });
      while (job.state === 'queued') {
        const phase = (job.progress || {}).phase || 'review';
        renderTimeline(phase, done);
        job = await api(job.advance_path, { method: 'POST' });
        if (job.state !== 'failed') done.add(phase);
      }
      if (job.state !== 'completed') {
        throw new Error(job.error || job.state);
      }
      currentJob = job;
      updateBadges(job.result);
      renderTab(pickDefaultTab(job.result));
      danmaku('审阅完成', 'done', 1800);
    } catch (err) {
      $('sc-error').textContent = err.message;
      $('sc-error').hidden = false;
      renderTab(activeTab);
      if (err.status === 401) {
        await saveToken('');
        showAuthView();
      }
    } finally {
      busy = false;
      $('sc-run').disabled = false;
    }
  }

  function showAuthView() {
    $('sc-app').hidden = true;
    $('sc-logout').hidden = true;
    $('sc-login-card').hidden = false;
  }

  async function showAppView() {
    $('sc-login-card').hidden = true;
    $('sc-app').hidden = false;
    $('sc-logout').hidden = false;
    await autoAttachConversation();
  }

  function bindUI() {
    document.querySelectorAll('input[name="sc-how"]').forEach((r) => {
      r.addEventListener('change', () => {
        const isManual = document.querySelector('input[name="sc-how"]:checked').value === 'manual';
        $('sc-manual-modes').hidden = !isManual;
      });
    });

    document.querySelectorAll('.sc-tab').forEach((b) => {
      b.addEventListener('click', () => renderTab(b.dataset.tab));
    });

    $('sc-run').addEventListener('click', startReview);
    $('sc-edit-toggle').addEventListener('click', () => {
      $('sc-ctx-drawer').hidden = !$('sc-ctx-drawer').hidden;
    });

    $('sc-pw-toggle')?.addEventListener('click', () => {
      const pw = $('sc-login-pw');
      pw.type = pw.type === 'password' ? 'text' : 'password';
    });

    $('sc-login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      $('sc-login-err').hidden = true;
      const btn = $('sc-login-btn');
      btn.disabled = true;
      try {
        const res = await api('/api/auth/login', {
          method: 'POST',
          body: { identifier: $('sc-login-id').value.trim(), password: $('sc-login-pw').value },
        });
        if (!res.authenticated) throw new Error('登录失败');
        if (res.session_token) await saveToken(res.session_token);
        await showAppView();
      } catch (err) {
        $('sc-login-err').textContent = err.message;
        $('sc-login-err').hidden = false;
      } finally {
        btn.disabled = false;
      }
    });

    $('sc-logout').addEventListener('click', async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
      await saveToken('');
      showAuthView();
    });
  }

  async function init() {
    bindUI();
    try {
      const me = await api('/api/auth/me');
      if (me && me.authenticated) await showAppView();
      else showAuthView();
    } catch (_) {
      showAuthView();
    }
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { startReview, applySnapshot, renderTab, init };
  }
  init();
})();
