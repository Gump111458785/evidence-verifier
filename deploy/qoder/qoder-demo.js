// Replace the Qoder site's original static-demo shim. Load before trisight.js.
(() => {
  const backend = '/functions/v1/app';
  const storageKey = 'trisight_competition_guest';
  const originalFetch = window.fetch.bind(window);
  let session;
  let pendingSession;

  function storedSession() {
    try { return JSON.parse(sessionStorage.getItem(storageKey)); }
    catch (_) { return null; }
  }

  async function getSession() {
    const saved = session || storedSession();
    if (saved?.guest_token && saved.expires_at * 1000 > Date.now()) return saved;
    if (!pendingSession) {
      pendingSession = (async () => {
        const response = await originalFetch(backend + '/api/competition/session', {
          method: 'POST', credentials: 'omit', redirect: 'error',
        });
        if (!response.ok) throw new Error('实时审阅暂不可用，可继续浏览案例库。');
        const data = await response.json();
        if (!data.guest_token || !Number.isFinite(data.expires_at)) throw new Error('访客会话未能初始化。');
        session = data;
        try { sessionStorage.setItem(storageKey, JSON.stringify(session)); } catch (_) {}
        return session;
      })().finally(() => { pendingSession = null; });
    }
    return pendingSession;
  }

  function reply(body, status = 200) {
    return new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  }

  const guestLabel = { username: 'guest', display_name: '访客体验', guest: true };
  window.fetch = async (input, init) => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) {
      return originalFetch(input, init);
    }
    const request = new Request(input instanceof Request ? input : url.href, init);
    const { method } = request;
    const path = url.pathname;
    if (method === 'GET' && path === '/api/auth/me') {
      // Existing page guards only use this local display label for navigation.
      return reply({ authenticated: true, guest_mode: true, user: guestLabel });
    }
    if (method === 'GET' && path === '/api/library') return originalFetch('/demo/library.json');
    const isReview = path === '/api/assist' || /^\/api\/assist\/[a-f0-9]{24}(?:\/(?:advance|cancel|feedback))?$/.test(path);
    if (!isReview && !(method === 'GET' && path === '/api/health')) {
      return reply({ error: '参赛界面仅开放案例库和访客审阅。' }, 403);
    }
    const headers = new Headers();
    try {
      if (isReview) {
        headers.set('Authorization', 'Bearer ' + (await getSession()).guest_token);
      }
      if (request.headers.has('Content-Type')) headers.set('Content-Type', request.headers.get('Content-Type'));
      const body = request.body ? await request.text() : undefined;
      const response = await originalFetch(backend + path + url.search, {
        method, headers, body, credentials: 'omit', redirect: 'error', signal: request.signal,
      });
      if (isReview && response.status === 401) {
        session = null;
        try { sessionStorage.removeItem(storageKey); } catch (_) {}
      }
      return response;
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      return reply({ code: 'backend_unavailable', error: '实时审阅服务暂时无法连接，可继续查看案例库。' }, 503);
    }
  };
})();
