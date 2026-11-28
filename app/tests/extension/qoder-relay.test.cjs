const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const relayUrl = pathToFileURL(
  path.resolve(__dirname, '../../../deploy/qoder/functions/relay.mjs'),
).href;

test('qoder relay handles logical and deployment-hashed function paths and isolates cookies', async () => {
  const { createRelay } = await import(relayUrl);
  const siteOrigin = 'https://trisight-sansheng-laed2yludt9.qoder.website';
  const calls = [];
  let responder = () => Response.json({ ok: true });
  const relay = createRelay(siteOrigin, 'https://evidence-verifier.edgeone.dev', async (url, opts) => {
    calls.push([url, opts]);
    return responder();
  });

  for (const prefix of [
    '/functions/v1/app',
    '/app',
    '/app-d0123456789abcdef0123456789abcdef',
    '/functions/v1/app-d0123456789abcdef0123456789abcdef',
  ]) {
    const res = await relay(new Request(siteOrigin + prefix + '/api/health'));
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('X-Trisight-Transport'), 'qoder-relay');
  }

  assert.equal((await relay(new Request(siteOrigin + '/functions/v1/other/api/health'))).status, 404);
  assert.equal((await relay(new Request(siteOrigin + '/functions/v1/app/api/auth/admin/users'))).status, 404);
  assert.equal((await relay(new Request(siteOrigin + '/functions/v1/app/api/assist'))).status, 401);
  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
      method: 'POST',
      headers: { Origin: 'https://elsewhere.example' },
    }))).status,
    403,
  );
  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
      method: 'POST',
      headers: { Origin: siteOrigin, 'Sec-Fetch-Site': 'cross-site' },
    }))).status,
    403,
  );
  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
      method: 'POST',
      headers: { Origin: siteOrigin },
    }))).status,
    200,
  );

  const token = 'Bearer guest.' + '0'.repeat(32) + '.0123456789.' + '1'.repeat(64);
  const res = await relay(new Request(
    siteOrigin + '/app-d' + 'a'.repeat(32) + '/api/assist?limit=30&target=untrusted',
    { headers: { Authorization: token, Cookie: 'ev_session=private' } },
  ));
  assert.equal(res.status, 200);
  const lastCall = calls.at(-1);
  assert.equal(lastCall[0].href, 'https://evidence-verifier.edgeone.dev/api/assist?limit=30');
  assert.equal(lastCall[1].headers.get('Cookie'), null);
  assert.equal(lastCall[1].headers.get('Authorization'), token);

  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/assist', {
      method: 'POST',
      body: 'x'.repeat(64001),
      headers: { Authorization: token, 'Content-Type': 'application/json' },
    }))).status,
    413,
  );

  responder = () => new Response('<html>401</html>', { status: 401, headers: { 'Content-Type': 'text/html' } });
  assert.equal((await relay(new Request(siteOrigin + '/functions/v1/app/api/health'))).status, 502);

  responder = () => Response.json({ ok: true });
  const extOrigin = 'chrome-extension://' + 'a'.repeat(32);
  const preflight = await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
    method: 'OPTIONS',
    headers: { Origin: extOrigin, 'Access-Control-Request-Method': 'POST' },
  }));
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('Access-Control-Allow-Origin'), extOrigin);

  const extSession = await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
    method: 'POST',
    headers: { Origin: extOrigin, 'Sec-Fetch-Site': 'cross-site' },
  }));
  assert.equal(extSession.status, 200);
  assert.equal(extSession.headers.get('Access-Control-Allow-Origin'), extOrigin);
  assert.equal(calls.at(-1)[1].headers.get('Origin'), siteOrigin);

  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
      method: 'OPTIONS',
      headers: { Origin: 'https://elsewhere.example' },
    }))).status,
    403,
  );
  assert.equal(
    (await relay(new Request(siteOrigin + '/functions/v1/app/api/competition/session', {
      method: 'POST',
      headers: { Origin: 'chrome-extension://q' + 'a'.repeat(31) },
    }))).status,
    403,
  );
});
