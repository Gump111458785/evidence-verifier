/* Trisight shared client: auth, navigation, API calls and result rendering. */
(() => {
  const TOKEN_KEY = 'ev_session_token';
  const REPO = 'https://github.com/Gump111458785/evidence-verifier';

  const token = () => {
    try { const t = sessionStorage.getItem(TOKEN_KEY) || ''; return /^[a-f0-9]{64}$/.test(t) ? t : ''; } catch (_) { return ''; }
  };
  const headers = (extra = {}) => (token() ? { ...extra, Authorization: 'Bearer ' + token() } : extra);

  async function api(path, options = {}) {
    const res = await fetch(path, {
      method: options.method || 'GET',
      credentials: 'same-origin',
      headers: headers(options.body ? { 'Content-Type': 'application/json' } : {}),
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || ('请求失败（' + res.status + '）'));
      err.status = res.status; err.code = data.code;
      throw err;
    }
    return data;
  }

  async function me() {
    try { const d = await api('/api/auth/me'); return d.authenticated ? d.user : null; } catch (_) { return null; }
  }

  function toLogin() {
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace('/login.html?reason=auth_required&next=' + next);
  }

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
    try { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem('ev_user_cache'); } catch (_) {}
    location.replace('/login.html?reason=logged_out');
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const MARK = '<svg class="ts-mark" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 6h9" stroke="#c03a2b" stroke-width="2.2" stroke-linecap="round"/><path d="M5 15l3-5 3 5" fill="none" stroke="#2353b8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M13 13h5m-2-2.5 2 2.5-2 2.5" fill="none" stroke="#0d7566" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  const PRESET_AVATARS = {
    'preset:ink': { bg: '#1c2230', fg: '#ffffff', label: '墨黑' },
    'preset:cobalt': { bg: '#2353b8', fg: '#ffffff', label: '群青' },
    'preset:emerald': { bg: '#0d7566', fg: '#ffffff', label: '松绿' },
    'preset:crimson': { bg: '#c03a2b', fg: '#ffffff', label: '朱红' },
    'preset:amber': { bg: '#94600c', fg: '#ffffff', label: '琥珀' },
    'preset:plum': { bg: '#6c3483', fg: '#ffffff', label: '紫檀' },
  };

  function avatarHtml(user, size = 28) {
    const name = String((user && (user.display_name || user.username)) || 'U').trim();
    const initial = esc((name[0] || 'U').toUpperCase());
    const av = String((user && user.avatar) || '').trim();
    const style = `width:${size}px;height:${size}px;font-size:${Math.max(11, Math.round(size * 0.44))}px;`;
    if (av.startsWith('data:image/') || av.startsWith('https://')) {
      return `<span class="ts-avatar" style="${style}"><img src="${esc(av)}" alt="${esc(name)}"></span>`;
    }
    const preset = PRESET_AVATARS[av] || PRESET_AVATARS['preset:ink'];
    return `<span class="ts-avatar" style="${style}background:${preset.bg};color:${preset.fg}">${initial}</span>`;
  }

  /* Top bar. `active` is the page key; pass user to show the account menu. */
  function bar(active, user) {
    const host = document.getElementById('ts-bar');
    if (!host) return;
    const links = user
      ? [['assist', '/assist.html', '审阅'], ['library', '/library.html', '案例与历史']]
      : [['home', '/', '首页'], ['library', '/library.html', '案例库']];
    host.innerHTML = '<div class="ts-bar">' +
      '<a class="ts-brand" href="/">' + MARK + '<b>Trisight</b><span>三省</span></a>' +
      '<nav class="ts-nav" aria-label="主导航">' + links.map(([k, href, label]) =>
        '<a href="' + href + '"' + (k === active ? ' aria-current="page"' : '') + '>' + label + '</a>').join('') + '</nav>' +
      '<div class="ts-user">' + (user
        ? '<div class="ts-user-menu" id="ts-user-menu">' +
            '<button class="ts-user-trigger" id="ts-user-trigger" type="button" aria-expanded="false" aria-haspopup="menu">' +
              avatarHtml(user, 26) +
              '<span class="ts-user-name">' + esc(user.display_name || user.username) + '</span>' +
              '<svg class="ts-caret" viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.2 4.2 6 8l3.8-3.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
            '</button>' +
            '<div class="ts-dropdown" id="ts-dropdown" role="menu" hidden>' +
              '<div class="ts-dropdown-head"><strong>' + esc(user.display_name || user.username) + '</strong><span>@' + esc(user.username) + (user.role === 'admin' ? ' · 管理员' : '') + '</span></div>' +
              '<a class="ts-dropdown-item' + (active === 'account' ? ' active' : '') + '" href="/account.html" role="menuitem">个人中心</a>' +
              '<button class="ts-dropdown-item danger" id="ts-logout" type="button" role="menuitem">退出登录</button>' +
            '</div>' +
          '</div>'
        : '<a href="' + REPO + '" rel="noopener">GitHub</a><a class="ts-btn small" href="/login.html?next=%2Fassist.html">登录</a>') +
      '</div></div>';
    const out = document.getElementById('ts-logout');
    if (out) out.addEventListener('click', logout);
    const trigger = document.getElementById('ts-user-trigger');
    const dropdown = document.getElementById('ts-dropdown');
    const menuWrap = document.getElementById('ts-user-menu');
    if (trigger && dropdown && menuWrap) {
      const closeMenu = () => {
        dropdown.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
      };
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = dropdown.hidden;
        dropdown.hidden = !open;
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      document.addEventListener('click', (e) => {
        if (!menuWrap.contains(e.target)) closeMenu();
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
      });
    }
  }

  /* Page guard: returns the user or redirects to login. */
  async function guard(active) {
    const user = await me();
    if (!user) { toLogin(); return null; }
    bar(active, user);
    return user;
  }

  // ---------------------------------------------------------------- rendering

  const VERDICT = {
    errors_found: '发现错误', no_error: '未发现错误', unverified: '部分断言未能核实',
    no_checkable_claims: '没有可核实的事实断言', not_run: '未运行',
  };
  const CLAIM = { contradicted: '有误', supported: '有证据支持', insufficient: '未能核实', invalid_output: '结果无效' };
  const SOURCE = { not_required: '解释类内容，无需来源', sourced: '已找到来源', unsourced: '事实类内容，未找到来源，请自行核实' };

  function marked(answer, claims) {
    const spans = (claims || []).filter((c) => Array.isArray(c.span)).sort((a, b) => a.span[0] - b.span[0]);
    let html = '', at = 0;
    for (const c of spans) {
      const [s, e] = c.span;
      if (s < at || answer.slice(s, e) !== c.text) continue;
      html += esc(answer.slice(at, s));
      html += '<span class="hl ' + c.verdict + '" title="' + esc(c.id + '：' + (CLAIM[c.verdict] || c.verdict)) + '">' + esc(answer.slice(s, e)) + '</span>';
      at = e;
    }
    return html + esc(answer.slice(at));
  }

  function quote(q) {
    if (!q || !q.text) return '';
    const where = q.url ? '<a href="' + esc(q.url) + '" rel="noopener" target="_blank">' + esc(q.title || q.url) + '</a>' : esc(q.title || '来源');
    return '<blockquote>“' + esc(q.text) + '”<br><span class="ts-meta">' + where + '</span></blockquote>';
  }

  function fbButtons(target, feedback, enabled) {
    if (!enabled) return '';
    const cur = (feedback || []).find((f) => f.target === target);
    const on = (v) => (cur && cur.value === v ? 'true' : 'false');
    return '<span class="fb"><button class="fbtn" data-fb="' + target + '" data-v="up" aria-pressed="' + on('up') + '" title="有用">有用</button>' +
      '<button class="fbtn" data-fb="' + target + '" data-v="down" aria-pressed="' + on('down') + '" title="没用">没用</button></span>';
  }

  function promptBlock(label, text) {
    if (!text) return '';
    return '<p class="ts-meta" style="margin:12px 0 0">' + label + '</p><div class="prompt-box"><pre>' + esc(text) +
      '</pre><button class="ts-btn quiet small" data-copy="' + esc(text) + '" type="button">复制</button></div>';
  }

  /* Render one result. opts: {question, answer, result, id, feedback, demo} */
  function render(host, opts) {
    const r = opts.result;
    if (!r) { host.innerHTML = '<p class="res-empty">还没有结果。</p>'; return; }
    if (r.status !== 'ok') {
      host.innerHTML = '<div class="ts-alert">本次审阅没有得到有效结果：' + esc(r.error || r.status) +
        '。这不代表回答没有问题。</div>';
      return;
    }
    const m = r.modes, fbOn = Boolean(opts.id) && !r.demo;
    const parts = [];
    parts.push('<div class="res-block"><div class="res-head"><h3>原回答</h3>' +
      (r.demo ? '<span class="demo-flag">演示：预设结果</span>' : '') + '<span class="why">' + esc(opts.question) + '</span></div>' +
      '<p class="res-answer">' + marked(opts.answer, m.correct.claims) + '</p></div>');

    // correction
    let c = '<div class="res-block"><div class="res-head"><h3><span class="dot correct"></span>纠错</h3>' +
      '<span class="verdict ' + m.correct.verdict + '">' + (VERDICT[m.correct.verdict] || m.correct.verdict) + '</span>' +
      '<span class="why">' + esc(m.correct.reason) + '</span>' + fbButtons('correct', opts.feedback, fbOn && m.correct.needed) + '</div>';
    for (const cl of m.correct.claims || []) {
      c += '<div class="claim ' + cl.verdict + '"><span class="tag">' + (CLAIM[cl.verdict] || cl.verdict) + '</span>“' + esc(cl.text) + '”' +
        (cl.explanation ? '<div class="ts-meta">' + esc(cl.explanation) + '</div>' : '') +
        (cl.correction ? '<div class="fix">更正：' + esc(cl.correction) + '</div>' : '') + quote(cl.quote) + '</div>';
    }
    c += promptBlock('交给原 AI 自我修正的提示词', r.prompts && r.prompts.correct) + '</div>';
    parts.push(c);

    // supplement
    let s = '<div class="res-block"><div class="res-head"><h3><span class="dot supplement"></span>补充</h3>' +
      '<span class="verdict ' + (m.supplement.needed ? '' : 'not_run') + '">' + (m.supplement.needed ? (m.supplement.points.length + ' 个遗漏点') : '无需补充') + '</span>' +
      '<span class="why">' + esc(m.supplement.reason) + '</span>' + fbButtons('supplement', opts.feedback, fbOn && m.supplement.needed) + '</div>';
    for (const p of m.supplement.points || []) {
      s += '<div class="point"><b>' + esc(p.point) + '</b>：' + esc(p.content) +
        '<span class="src">' + esc(p.why) + '。' + (SOURCE[p.source_status] || '') + '</span>' + quote(p.quote) + '</div>';
    }
    s += promptBlock('交给原 AI 补充的提示词', r.prompts && r.prompts.supplement) + '</div>';
    parts.push(s);

    // prediction
    let p = '<div class="res-block"><div class="res-head"><h3><span class="dot predict"></span>预测</h3>' +
      '<span class="verdict ' + (m.predict.needed ? '' : 'not_run') + '">' + (m.predict.needed ? '你接下来可能会问' : '无需预测') + '</span>' +
      '<span class="why">' + esc(m.predict.reason) + '</span>' + fbButtons('predict', opts.feedback, fbOn && m.predict.needed) + '</div>';
    if ((m.predict.questions || []).length) {
      p += '<div class="nextq">' + m.predict.questions.map((q) => '<button type="button" data-copy="' + esc(q.question) + '" title="点击复制">' + esc(q.question) + '</button>').join('') + '</div>';
    }
    parts.push(p + '</div>');

    // our answer
    if (r.answer) {
      let a = '<div class="res-block"><div class="res-head"><h3>修订后的回答</h3>' + fbButtons('answer', opts.feedback, fbOn) + '</div>';
      if (r.answer.status === 'ok') {
        a += '<div class="revised">' + esc(r.answer.revised_answer) + '</div><ul class="changes">' +
          r.answer.changes.map((ch) => '<li>' + esc(ch.summary) + '（依据 ' + ch.finding_ids.map(esc).join('、') + '）</li>').join('') + '</ul>';
      } else {
        a += '<div class="ts-note">修订稿无效：' + esc(r.answer.error) + '。可以改用上面的提示词交给原 AI 修改。</div>';
      }
      parts.push(a + '</div>');
    }
    if (r.usage && !r.demo) {
      parts.push('<p class="ts-meta">本次调用：模型 ' + r.usage.model_calls + ' 次，检索 ' + r.usage.search_calls + ' 次。纠错只检查开放式事实断言，结构化关系核验尚未接入。</p>');
    }
    host.innerHTML = parts.join('');
    host.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); const t = b.textContent; b.textContent = '已复制'; setTimeout(() => { b.textContent = t; }, 1200); } catch (_) {}
    }));
    host.querySelectorAll('[data-fb]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const d = await api('/api/assist/' + opts.id + '/feedback', { method: 'POST', body: { target: b.dataset.fb, value: b.dataset.v } });
        opts.feedback = d.feedback;
        host.querySelectorAll('[data-fb="' + b.dataset.fb + '"]').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
      } catch (e) { alertIn(host, e.message); }
    }));
  }

  function alertIn(host, message) {
    const box = document.createElement('div');
    box.className = 'ts-alert'; box.setAttribute('role', 'alert'); box.textContent = message;
    host.prepend(box);
  }

  const time = (ts) => { try { return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }); } catch (_) { return ''; } };

  window.TS = { api, me, guard, bar, render, esc, time, logout, REPO, alertIn, avatarHtml, PRESET_AVATARS };
})();
