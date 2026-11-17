const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const code = fs.readFileSync(path.join(__dirname, '../../static/sidecar.js'), 'utf8');

function makeNode(overrides = {}) {
  return {
    value: '',
    hidden: false,
    disabled: false,
    checked: false,
    textContent: '',
    innerHTML: '',
    dataset: {},
    attrs: {},
    listeners: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(type, fn) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(fn);
    },
    querySelectorAll() { return []; },
    appendChild() {},
    ...overrides,
  };
}

function harness(options = {}) {
  const nodes = new Map();
  const requests = [];
  const pageCommands = [];
  const tabs = ['correct', 'supplement', 'predict', 'answer'].map((t) =>
    makeNode({ dataset: { tab: t }, attrs: { 'aria-selected': t === 'correct' ? 'true' : 'false' } })
  );
  const howAuto = makeNode({ value: 'auto', checked: options.how !== 'manual' });
  const howManual = makeNode({ value: 'manual', checked: options.how === 'manual' });
  const modeBoxes = ['correct', 'supplement', 'predict'].map((m) =>
    makeNode({ value: m, checked: (options.modes || ['correct', 'supplement', 'predict']).includes(m) })
  );

  const get = (id) => {
    if (!nodes.has(id)) nodes.set(id, makeNode());
    return nodes.get(id);
  };

  const completedJob = options.job || {
    id: 'job-m2-01',
    state: 'completed',
    result: {
      status: 'ok',
      modes: {
        correct: {
          needed: true,
          verdict: 'errors_found',
          claims: [{ id: 'C1', text: '1899年建成', verdict: 'contradicted', correction: '1889年建成', quote: { text: '1889年3月', title: '史料' } }],
        },
        supplement: {
          needed: true,
          points: [{ id: 'M1', point: '博览会拱门', content: '最初为世界博览会入口。' }],
        },
        predict: {
          needed: true,
          questions: [{ id: 'Q1', question: '铁塔为何差点被拆除？' }],
        },
      },
      prompts: {
        correct: '请更正为1889年',
        supplement: '请补充博览会背景',
      },
      answer: { status: 'ok', revised_answer: '埃菲尔铁塔于1889年建成，最初为世博会入口。' },
    },
  };

  const context = {
    location: { protocol: options.protocol || 'chrome-extension:', origin: 'chrome-extension://abcdefghijklmnopabcdefghijklmnop' },
    AbortSignal: { timeout: () => undefined },
    setTimeout,
    clearTimeout,
    console,
    navigator: { clipboard: { writeText: async () => {} } },
    module: { exports: {} },
    document: {
      body: { appendChild() {} },
      getElementById: get,
      createElement: () => makeNode(),
      querySelector(sel) {
        if (sel === 'input[name="sc-how"]:checked') return howManual.checked ? howManual : howAuto;
        if (sel === '.sc-danmaku-stage') return makeNode();
        return null;
      },
      querySelectorAll(sel) {
        if (sel === 'input[name="sc-how"]') return [howAuto, howManual];
        if (sel === 'input[name="sc-mode"]:checked') return modeBoxes.filter((b) => b.checked);
        if (sel === '.sc-tab') return tabs;
        return [];
      },
    },
    chrome: {
      storage: {
        local: {
          get: async () => ({ ev_session_token: 'a'.repeat(64) }),
          set: async () => {},
          remove: async () => {},
        },
        session: {
          get: async () => ({}),
        },
        onChanged: { addListener() {} },
      },
      runtime: {
        sendMessage: async (msg) => {
          pageCommands.push(msg);
          return {
            ok: true,
            value: { platform: 'ChatGPT', question: '铁塔哪年建成？', candidate: '1899年建成。' },
          };
        },
      },
    },
    fetch: async (url, opts = {}) => {
      requests.push({ url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
      if (url.endsWith('/api/auth/me')) {
        return { ok: true, status: 200, json: async () => ({ authenticated: true, user: { id: 'u1', username: 'ev_admin', role: 'admin' } }) };
      }
      if (url.endsWith('/api/assist') && opts.method === 'POST') {
        return { ok: true, status: 200, json: async () => ({ id: 'job-m2-01', state: 'queued', advance_path: '/api/assist/job-m2-01/advance', progress: { phase: 'review' } }) };
      }
      if (url.endsWith('/advance') && opts.method === 'POST') {
        return { ok: true, status: 200, json: async () => completedJob };
      }
      throw new Error('Unexpected fetch ' + url);
    },
  };

  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, get, requests, pageCommands, tabs, howAuto, howManual, modeBoxes };
}

test('extension auto-watches active conversation on init without manual capture button', async () => {
  const f = harness();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(f.pageCommands[0].mode, 'watch');
  assert.equal(f.get('question').value, '铁塔哪年建成？');
  assert.equal(f.get('candidate').value, '1899年建成。');
  assert.equal(f.get('sc-sync-text').textContent, 'ChatGPT · 已同步');
});

test('auto mode sends all three modes [correct, supplement, predict]', async () => {
  const f = harness({ how: 'auto' });
  await new Promise((r) => setTimeout(r, 20));
  await f.context.module.exports.startReview();
  const createReq = f.requests.find((r) => r.url.endsWith('/api/assist') && r.method === 'POST');
  assert.deepEqual(createReq.body.modes, ['correct', 'supplement', 'predict']);
  assert.equal(createReq.body.client, 'extension');
  assert.equal(f.get('b-correct').textContent, '1');
  assert.equal(f.get('b-supplement').textContent, '1');
  assert.equal(f.get('b-predict').textContent, '1');
});

test('manual mode sends only user-selected subset and tabs render selectively', async () => {
  const f = harness({ how: 'manual', modes: ['correct', 'predict'] });
  await new Promise((r) => setTimeout(r, 20));
  await f.context.module.exports.startReview();
  const createReq = f.requests.find((r) => r.url.endsWith('/api/assist') && r.method === 'POST');
  assert.deepEqual(createReq.body.modes, ['correct', 'predict']);

  f.context.module.exports.renderTab('predict');
  assert.ok(f.get('sc-pane').innerHTML.includes('铁塔为何差点被拆除？'));
  assert.ok(!f.get('sc-pane').innerHTML.includes('博览会拱门'));

  f.context.module.exports.renderTab('supplement');
  assert.ok(f.get('sc-pane').innerHTML.includes('博览会拱门'));
});
