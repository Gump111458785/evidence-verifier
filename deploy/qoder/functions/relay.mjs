const DEFAULT_SITE_ORIGIN = 'https://trisight-sansheng-laed2yludt9.qoder.website';
const DEFAULT_UPSTREAM = 'https://evidence-verifier.edgeone.dev';
const FUNCTION_PREFIX = /^\/(?:functions\/v1\/)?app(?:-d[0-9a-f]{32})?(?=\/|$)/;
const SESSION_ROUTE = '/api/competition/session';
const GUEST_TOKEN = /^Bearer guest\.[a-f0-9]{32}\.[0-9]{1,12}\.[a-f0-9]{64}$/;
const REVIEW_RECORD = /^\/api\/assist\/[a-f0-9]{24}$/;
const REVIEW_ACTION = /^\/api\/assist\/[a-f0-9]{24}\/(?:advance|cancel|feedback)$/;
const MAX_BODY_BYTES = 64_000;

function fail(status, code, error) {
  return Response.json({ code, error }, { status, headers: { 'Cache-Control': 'no-store' } });
}

export function createRelay(
  siteOrigin = DEFAULT_SITE_ORIGIN,
  upstream = DEFAULT_UPSTREAM,
  fetchUpstream = fetch,
) {
  return async function relay(request) {
    const url = new URL(request.url);
    const path = url.pathname.replace(FUNCTION_PREFIX, '');
    if (path === url.pathname) return fail(404, 'route_not_found', '接口不存在。');

    const method = request.method;
    const health = method === 'GET' && path === '/api/health';
    const session = method === 'POST' && path === SESSION_ROUTE;
    const review =
      (method === 'GET' && (path === '/api/assist' || REVIEW_RECORD.test(path))) ||
      (method === 'POST' && (path === '/api/assist' || REVIEW_ACTION.test(path)));
    if (!health && !session && !review) return fail(404, 'route_not_found', '接口不存在。');

    const origin = request.headers.get('Origin');
    if (request.headers.get('Sec-Fetch-Site') === 'cross-site' || (origin && origin !== siteOrigin)) {
      return fail(403, 'origin_denied', '请从参赛站点提交审阅。');
    }
    const authorization = request.headers.get('Authorization') ?? '';
    if (review && !GUEST_TOKEN.test(authorization)) {
      return fail(401, 'guest_session_invalid', '访客会话已失效。');
    }

    const target = new URL(path, upstream);
    if (method === 'GET' && path === '/api/assist') {
      for (const key of ['offset', 'limit']) {
        const value = url.searchParams.get(key);
        if (value !== null && /^\d{1,7}$/.test(value)) target.searchParams.set(key, value);
      }
    }
    const headers = new Headers({ Accept: 'application/json', Origin: siteOrigin });
    if (review) headers.set('Authorization', authorization);

    try {
      let body;
      if (method === 'POST' && request.body) {
        if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) {
          return fail(413, 'input_too_large', '审阅材料超过大小限制。');
        }
        body = await request.arrayBuffer();
        if (body.byteLength > MAX_BODY_BYTES) return fail(413, 'input_too_large', '审阅材料超过大小限制。');
        if (body.byteLength) {
          if (!request.headers.get('Content-Type')?.startsWith('application/json')) {
            return fail(415, 'json_required', '请使用 JSON 提交审阅材料。');
          }
          headers.set('Content-Type', 'application/json');
        }
      }
      const response = await fetchUpstream(target, {
        method,
        headers,
        body,
        redirect: 'error',
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(40_000)]),
      });
      if (!response.headers.get('Content-Type')?.includes('application/json')) {
        await response.body?.cancel();
        return fail(502, 'upstream_unavailable', '审阅服务暂时不可达，可先查看案例库。');
      }
      return new Response(response.body, {
        status: response.status,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store',
          'X-Content-Type-Options': 'nosniff',
          'X-Trisight-Transport': 'qoder-relay',
        },
      });
    } catch (_) {
      return fail(502, 'upstream_unavailable', '审阅服务暂时不可达，请稍后查看历史或浏览案例库。');
    }
  };
}

export const relay = createRelay();
