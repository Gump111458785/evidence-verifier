const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const code = fs.readFileSync(path.join(__dirname, '../../static/sidecar.js'), 'utf8');

function node(extra = {}) {
  return {
    value: '', hidden: false, disabled: false, textContent: '', innerHTML: '', className: '', title: '',
    dataset: {}, attrs: {}, listeners: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    click() { (this.listeners.click || []).forEach((fn) => fn({ preventDefault() {} })); },
    querySelectorAll() { return []; },
    ...extra,
  };
}

const RESULT = {
  status: 'ok',
  modes: {
    correct: { needed: true, verdict: 'errors_found', claims: [{ id: 'C1', text: '1899年建成', verdict: 'contradicted', correction: '1889年建成', quote: { text: 'completed in 1889', url: 'https://example.org' } }] },
    supplement: { needed: true, points: [{ id: 'M1', point: '博览会拱门', content: '最初为世界博览会入口。', source_status: 'not_required' }] },
    predict: { needed: true, questions: [{ id: 'Q1', question: '铁塔为何差点被拆除？' }] },
  },
  prompts: { correct: '请更正为1889年', supplement: '请补充博览会背景' },
  answer: null,
};

function harness(opts = {}) {
  const nodes = new Map();
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const tabs = ['correct', 'supplement', 'predict'].map((t) => node({ dataset: { tab: t }, disabled: true }));
  const htabs = ['correct', 'supplement', 'predict'].map((t) => node({ dataset: { tab: t }, disabled: true }));
  const navs = ['review', 'history'].map((v) => node({ dataset: { view: v } }));
  const chips = ['correct', 'supplement', 'predict'].map((m) => node({ dataset: { mode: m }, attrs: { 'aria-pressed': 'true' } }));
  get('meter').querySelectorAll = () => [node(), node(), node()];
  get('run-manual').parentElement = node();
  const storageData = {};
  const guestToken = 'guest.' + '0'.repeat(32) + '.9999999999.' + '1'.repeat(64);
  const requests = [], commands = [];
  const snapshot = opts.snapshot === undefined
    ? { platform: 'ChatGPT', question: '铁塔哪年建成？', candidate: '1899年建成。', context: [{ role: 'user', text: '介绍巴黎' }], tab_id: 7 }
    : opts.snapshot;
  const context = {
    location: { protocol: 'chrome-extension:', origin: 'chrome-extension://x' },
    AbortSignal: { timeout: () => undefined }, setTimeout, clearTimeout, console,
    navigator: { clipboard: { writeText: async () => {} } },
    module: { exports: {} },
    document: {
      getElementById: get,
      querySelectorAll: (s) => (s === '.tab' ? tabs : s === '.htab' ? htabs : s === '.nav-btn' ? navs : s === '.chip' ? chips : []),
    },
    chrome: {
      storage: {
        local: {
          get: async (k) => (typeof k === 'string' ? (k in storageData ? { [k]: storageData[k] } : {}) : { ...storageData }),
          set: async (obj) => Object.assign(storageData, obj),
          remove: async (k) => { delete storageData[k]; },
        },
        session: { get: async () => (snapshot ? { liveSnapshot: snapshot } : {}) },
        onChanged: { addListener(fn) { context.__onChanged = fn; } },
      },
      runtime: { sendMessage: async (m) => { commands.push(m); return { ok: true, value: m.mode === 'insert' ? { inserted: true, sent: false } : snapshot || {} }; } },
    },
    fetch: async (url, o = {}) => {
      requests.push({ url, method: o.method || 'GET', headers: o.headers || {}, body: o.body ? JSON.parse(o.body) : null });
      if (url.endsWith('/api/competition/session')) return { ok: true, status: 200, json: async () => ({ guest_token: guestToken, expires_at: 9999999999 }) };
      if (url.includes('/api/assist?limit=')) return { ok: true, status: 200, json: async () => ({ total: 1, rows: [{ id: 'j1', state: 'completed', status: 'ok', created: 1794986000, question: '铁塔哪年建成？', correct: 'errors_found', supplement: 1, predict: 1 }] }) };
      if (url.endsWith('/api/assist/j1')) return { ok: true, status: 200, json: async () => ({ id: 'j1', state: 'completed', created: 1794986000, input: { question: '铁塔哪年建成？', answer: '1899年建成。', modes: 'auto' }, result: RESULT }) };
      if (url.endsWith('/api/assist')) return { ok: true, status: 200, json: async () => ({ id: 'j1', state: 'queued', advance_path: '/api/assist/j1/advance', progress: { phase: 'review' } }) };
      if (url.endsWith('/advance')) return { ok: true, status: 200, json: async () => ({ id: 'j1', state: 'completed', result: RESULT }) };
      throw new Error('unexpected ' + url);
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, get, tabs, htabs, navs, chips, requests, commands, api: context.module.exports };
}
const settle = () => new Promise((r) => setTimeout(r, 20));

test('opening the panel uses the pushed snapshot: no page read, Q&A folded, buttons ready', async () => {
  const f = harness();
  await settle();
  assert.equal(f.commands.length, 0);
  assert.equal(f.get('question').value, '铁塔哪年建成？');
  assert.notEqual(f.get('src').attrs['aria-expanded'], 'true');
  assert.equal(f.get('src').dataset.state, 'ready');
  assert.equal(f.get('run-auto').disabled, false);
});

test('without a snapshot the panel asks the page once, in the background', async () => {
  const f = harness({ snapshot: null });
  await settle();
  assert.equal(f.commands[0].mode, 'capture');
  assert.equal(f.get('run-auto').disabled, true);
});

test('auto review sends all three modes with captured context and no compose, without re-reading the page', async () => {
  const f = harness();
  await settle();
  f.get('run-auto').click();
  await settle();
  const create = f.requests.find((r) => r.url.endsWith('/api/assist'));
  assert.deepEqual(create.body.modes, ['correct', 'supplement', 'predict']);
  assert.equal(create.body.compose, false);
  assert.equal(create.body.client, 'extension');
  assert.deepEqual(create.body.context, [{ role: 'user', text: '介绍巴黎' }]);
  assert.equal(f.commands.length, 0);
  assert.equal(f.get('n-correct').textContent, '1');
  assert.ok(f.get('pane').innerHTML.includes('1889年建成'));
  assert.ok(f.tabs.every((t) => t.disabled === false));
});

test('manual review: first press reveals choices, second press runs only the chosen ones', async () => {
  const f = harness();
  await settle();
  f.get('run-manual').click();
  assert.equal(f.get('chips').hidden, false);
  assert.equal(f.requests.filter((r) => r.url.endsWith('/api/assist')).length, 0);
  f.chips[1].click();
  f.get('run-manual').click();
  await settle();
  const create = f.requests.find((r) => r.url.endsWith('/api/assist'));
  assert.deepEqual(create.body.modes, ['correct', 'predict']);
  assert.equal(f.tabs[1].disabled, true);
  f.api.renderTab('predict');
  assert.ok(f.get('pane').innerHTML.includes('铁塔为何差点被拆除？'));
});

test('a new answer on the page clears the old result so nothing stale is shown', async () => {
  const f = harness();
  await settle();
  f.get('run-auto').click();
  await settle();
  f.context.__onChanged({ liveSnapshot: { newValue: { platform: 'ChatGPT', question: '新问题', candidate: '新回答' } } }, 'session');
  assert.equal(f.get('question').value, '新问题');
  assert.ok(f.tabs.every((t) => t.disabled === true));
  assert.equal(f.api.state.job, null);
});

test('prompts are offered as fill actions on the result', async () => {
  const f = harness();
  await settle();
  f.get('run-auto').click();
  await settle();
  f.api.renderTab('correct');
  assert.ok(f.get('pane').innerHTML.includes('data-fill="请更正为1889年"'));
});

test('pull-ctx button actively captures earlier conversation context and updates status', async () => {
  const f = harness();
  await settle();
  assert.equal(f.commands.length, 0);
  f.get('pull-ctx').click();
  await settle();
  assert.equal(f.commands.length, 1);
  assert.equal(f.commands[0].mode, 'capture');
  assert.ok(f.get('ctx-status').textContent.includes('1 段'));
});

test('api retries transient Failed to fetch network drops before succeeding', async () => {
  const f = harness();
  await settle();
  let assistDrops = 0;
  const origFetch = f.context.fetch;
  f.context.fetch = async (url, o) => {
    if (url.endsWith('/api/assist') && assistDrops === 0) {
      assistDrops += 1;
      throw new TypeError('Failed to fetch');
    }
    return origFetch(url, o);
  };
  f.get('run-auto').click();
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(f.get('alert').hidden, true);
  assert.ok(f.get('pane').innerHTML.includes('1889年建成'));
});

test('toggle-qa expands and hides extracted QA drawer cleanly', async () => {
  const f = harness();
  f.get('drawer').hidden = true;
  await settle();
  assert.equal(f.get('drawer').hidden, true);
  f.get('toggle-qa').click();
  assert.equal(f.get('drawer').hidden, false);
  assert.equal(f.get('toggle-qa').textContent, '收起 QA');
  f.get('toggle-qa').click();
  assert.equal(f.get('drawer').hidden, true);
  assert.equal(f.get('toggle-qa').textContent, '查看 QA');
});

test('api retries transient non-JSON 500 gateway errors before succeeding', async () => {
  const f = harness();
  await settle();
  let calls = 0;
  const origFetch = f.context.fetch;
  f.context.fetch = async (url, o) => {
    if (url.endsWith('/advance') && calls === 0) {
      calls += 1;
      return { ok: false, status: 500, json: async () => { throw new Error('html'); } };
    }
    return origFetch(url, o);
  };
  f.get('run-auto').click();
  await new Promise((r) => setTimeout(r, 450));
  assert.equal(f.get('alert').hidden, true);
  assert.ok(f.get('pane').innerHTML.includes('1889年建成'));
});

test('pull-ctx reveals ctx-drawer with rendered turns and toggles cleanly', async () => {
  const f = harness();
  f.get('ctx-drawer').hidden = true;
  await settle();
  f.get('pull-ctx').click();
  await settle();
  assert.equal(f.get('ctx-drawer').hidden, false);
  assert.ok(f.get('ctx-list').innerHTML.includes('介绍巴黎'));
  f.get('toggle-ctx').click();
  assert.equal(f.get('ctx-drawer').hidden, true);
});

test('history view lists past reviews and opens detail tabs cleanly', async () => {
  const f = harness();
  await settle();
  f.api.switchView('history');
  await settle();
  assert.equal(f.get('work').hidden, true);
  assert.equal(f.get('history').hidden, false);
  assert.ok(f.get('hist-list').innerHTML.includes('铁塔哪年建成？'));
  await f.api.openHistory('j1');
  assert.equal(f.get('hist-detail-wrap').hidden, false);
  assert.ok(f.get('hpane').innerHTML.includes('1889年建成'));
  f.api.renderHistTab('predict');
  assert.ok(f.get('hpane').innerHTML.includes('铁塔为何差点被拆除？'));
  f.api.switchView('review');
  assert.equal(f.get('work').hidden, false);
  assert.equal(f.get('history').hidden, true);
});

test('unverified claims display as correct (green supported) and only contradicted items are marked red', async () => {
  const f = harness();
  await settle();
  f.api.state.job = {
    result: {
      status: 'ok',
      modes: {
        correct: {
          needed: true,
          verdict: 'unverified',
          claims: [
            { id: 'C1', text: '未能核实断言A', verdict: 'insufficient', explanation: '无证据' },
            { id: 'C2', text: '有误断言B', verdict: 'contradicted', correction: '正确B', quote: { text: 'q', url: 'https://example.org' } },
            { id: 'C3', text: '有证据支持的断言C', verdict: 'supported', quote: { text: 'supporting quote', url: 'https://example.org' } },
          ],
        },
        supplement: { needed: false, points: [] },
        predict: { needed: false, questions: [] },
      },
      prompts: {},
    },
  };
  f.api.renderTab('correct');
  const html = f.get('pane').innerHTML;
  assert.ok(!html.includes('未能核实"'));
  assert.ok(html.includes('<div class="item supported">未能核实断言A</div>'));
  assert.ok(html.includes('<div class="item contradicted"><del>有误断言B</del>'));
  assert.ok(html.includes('<div class="item supported">有证据支持的断言C'));
});

test('extension starts without login via Qoder relay and refreshes guest token on 401', async () => {
  const f = harness();
  await settle();
  assert.equal(f.get('login').hidden, true);
  assert.equal(f.get('logout').hidden, true);
  assert.equal(f.get('work').hidden, false);
  assert.equal(f.requests.length, 0);

  let rejectedOnce = false;
  const origFetch = f.context.fetch;
  f.context.fetch = async (url, o = {}) => {
    if (url.endsWith('/api/assist') && !rejectedOnce) {
      rejectedOnce = true;
      return { ok: false, status: 401, json: async () => ({ code: 'guest_session_invalid', error: '访客会话已失效' }) };
    }
    return origFetch(url, o);
  };
  f.get('run-auto').click();
  await settle();
  const sessionCalls = f.requests.filter((r) => r.url === 'https://trisight-sansheng-laed2yludt9.qoder.website/functions/v1/app/api/competition/session');
  const assistCall = f.requests.find((r) => r.url === 'https://trisight-sansheng-laed2yludt9.qoder.website/functions/v1/app/api/assist');
  assert.equal(sessionCalls.length, 2);
  assert.ok(assistCall.headers.Authorization.startsWith('Bearer guest.'));
  assert.equal(f.get('login').hidden, true);
  assert.equal(f.get('alert').hidden, true);
  assert.ok(f.get('pane').innerHTML.includes('1889年建成'));
});
